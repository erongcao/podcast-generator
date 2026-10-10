"""Tests for MiniMax streaming synthesis (synthesize_streaming / collect_stream).

Pure-Python tests with mocked requests. Live API path is exercised by hand
probes (see /tmp/stream_full.txt from curl probe) — these unit tests verify
the generator, final-event detection, error propagation, and the
collect_stream convenience for saving to disk.
"""
import json
from unittest.mock import MagicMock, patch

import pytest
import requests

import minimax_tts


# ===== Helpers =====

def _make_sse_response(lines: list) -> MagicMock:
    """Build a mock requests.Response that yields lines on iter_lines().

    Each line should be a single SSE event line, e.g. 'data: {...}'.
    """
    resp = MagicMock(spec=requests.Response)
    resp.status_code = 200
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    resp.raise_for_status = MagicMock()
    resp.iter_lines = MagicMock(return_value=iter(lines))
    resp.close = MagicMock()
    return resp


def _patch_post(mock_resp):
    """Context manager: patch minimax_tts.requests.post to return mock_resp."""
    return patch("minimax_tts.requests.post", return_value=mock_resp)


# ===== Tests for synthesize_streaming (yield-based) =====

def test_streaming_yields_each_chunk_with_final_flag():
    """Each SSE event becomes one yielded chunk; final chunk is flagged."""
    lines = [
        'data: {"data":{"audio":"48656c6c6f","status":1},"base_resp":{"status_code":0}}',  # "Hello"
        'data: {"data":{"audio":"20576f726c64","status":1},"base_resp":{"status_code":0}}',  # " World"
        'data: {"data":{"audio":"deadbeef","status":2},"base_resp":{"status_code":0},"extra_info":{"audio_size":9}}',  # final
    ]
    mock_resp = _make_sse_response(lines)

    with _patch_post(mock_resp):
        chunks = list(minimax_tts._synthesize_api_stream(
            "hello world", "male-qn-qingse", "sk-test",
        ))

    # 3 chunks total; only last is final
    assert len(chunks) == 3
    assert chunks[0] == (b"Hello", False, chunks[0][2])
    assert chunks[1] == (b" World", False, chunks[1][2])
    audio, is_final, event = chunks[2]
    assert audio == b"\xde\xad\xbe\xef"
    assert is_final is True
    assert event["extra_info"]["audio_size"] == 9


def test_streaming_stops_after_final_event():
    """After status=2 the generator should return, not consume further lines."""
    # If we put a 4th line after final, it should not be processed.
    lines = [
        'data: {"data":{"audio":"4142","status":2},"base_resp":{"status_code":0}}',
        # Should be ignored — generator should have returned
        'data: {"data":{"audio":"ignored","status":2},"base_resp":{"status_code":0}}',
    ]
    mock_resp = _make_sse_response(lines)
    with _patch_post(mock_resp):
        chunks = list(minimax_tts._synthesize_api_stream(
            "t", "v", "k",
        ))
    assert len(chunks) == 1


def test_streaming_skips_empty_and_non_data_lines():
    """Blank lines and lines without 'data:' prefix are ignored."""
    lines = [
        "",
        'event: ping',  # not a data line, should be skipped
        'data: {"data":{"audio":"414243","status":1},"base_resp":{"status_code":0}}',
        "",
        'data: {"data":{"audio":"","status":2},"base_resp":{"status_code":0}}',  # final, empty audio
    ]
    mock_resp = _make_sse_response(lines)
    with _patch_post(mock_resp):
        chunks = list(minimax_tts._synthesize_api_stream(
            "t", "v", "k",
        ))
    assert len(chunks) == 1
    assert chunks[0][0] == b"ABC"


def test_streaming_raises_api_error_on_non_zero_status():
    """base_resp.status_code != 0 should raise APIError."""
    from exceptions import APIError

    lines = [
        'data: {"base_resp":{"status_code":1,"status_msg":"rate limited"},"trace_id":"abc"}',
    ]
    mock_resp = _make_sse_response(lines)
    with _patch_post(mock_resp):
        with pytest.raises(APIError) as exc_info:
            list(minimax_tts._synthesize_api_stream("t", "v", "k"))
    assert "rate limited" in str(exc_info.value)
    assert exc_info.value.trace_id == "abc"


def test_streaming_skips_malformed_json_lines():
    """Bad JSON should be logged and skipped, not crash the generator."""
    lines = [
        'data: not-valid-json',
        'data: {"data":{"audio":"4142","status":1},"base_resp":{"status_code":0}}',
        'data: {"data":{"audio":"deadbeef","status":2},"base_resp":{"status_code":0}}',
    ]
    mock_resp = _make_sse_response(lines)
    with _patch_post(mock_resp):
        chunks = list(minimax_tts._synthesize_api_stream(
            "t", "v", "k",
        ))
    # The malformed line is skipped; the two valid ones are yielded.
    assert len(chunks) == 2
    assert chunks[0][0] == b"AB"
    assert chunks[1][1] is True  # final flag


def test_streaming_passes_stream_true_in_payload():
    """Verify the request payload includes stream=True and required fields."""
    mock_resp = _make_sse_response([
        'data: {"data":{"audio":"","status":2},"base_resp":{"status_code":0}}',
    ])
    with _patch_post(mock_resp) as post_mock:
        list(minimax_tts._synthesize_api_stream(
            "hello", "male-qn-qingse", "sk-test",
            model="speech-2.8-turbo", speed=1.2, vol=0.9, pitch=2,
            emotion="happy", language_boost="Chinese",
        ))
    call_kwargs = post_mock.call_args.kwargs
    assert call_kwargs["stream"] is True
    assert call_kwargs["timeout"] == 60
    payload = call_kwargs["json"]
    assert payload["stream"] is True
    assert payload["model"] == "speech-2.8-turbo"
    assert payload["text"] == "hello"
    assert payload["voice_setting"]["voice_id"] == "male-qn-qingse"
    assert payload["voice_setting"]["speed"] == 1.2
    assert payload["voice_setting"]["emotion"] == "happy"
    assert payload["output_format"] == "hex"
    assert payload["language_boost"] == "Chinese"


def test_streaming_omits_emotion_when_empty():
    """Empty emotion should not appear in voice_setting."""
    mock_resp = _make_sse_response([
        'data: {"data":{"audio":"","status":2},"base_resp":{"status_code":0}}',
    ])
    with _patch_post(mock_resp) as post_mock:
        list(minimax_tts._synthesize_api_stream(
            "t", "v", "k", emotion="",
        ))
    payload = post_mock.call_args.kwargs["json"]
    assert "emotion" not in payload["voice_setting"]


# ===== Tests for collect_stream =====

def test_collect_stream_returns_final_audio():
    """collect_stream should return only the status=2 audio (not chunks)."""
    lines = [
        'data: {"data":{"audio":"414243","status":1},"base_resp":{"status_code":0}}',  # "ABC"
        'data: {"data":{"audio":"444546","status":1},"base_resp":{"status_code":0}}',  # "DEF"
        'data: {"data":{"audio":"deadbeef","status":2},"base_resp":{"status_code":0},"extra_info":{"audio_size":4}}',
    ]
    mock_resp = _make_sse_response(lines)
    with _patch_post(mock_resp):
        audio, evt = minimax_tts.collect_stream("t", "v", "k")
    # Final event's audio is what's saved, NOT the concatenated chunks.
    assert audio == b"\xde\xad\xbe\xef"
    assert evt["extra_info"]["audio_size"] == 4


def test_collect_stream_raises_if_no_final_event():
    """Without a status=2 event, collect_stream should raise."""
    lines = [
        'data: {"data":{"audio":"4142","status":1},"base_resp":{"status_code":0}}',
    ]
    mock_resp = _make_sse_response(lines)
    with _patch_post(mock_resp):
        with pytest.raises(RuntimeError, match="status=2"):
            minimax_tts.collect_stream("t", "v", "k")


def test_synthesize_streaming_alias_works():
    """synthesize_streaming is the public entry point."""
    mock_resp = _make_sse_response([
        'data: {"data":{"audio":"4142","status":2},"base_resp":{"status_code":0}}',
    ])
    with _patch_post(mock_resp):
        chunks = list(minimax_tts.synthesize_streaming(
            "t", "v", "k",
        ))
    assert chunks[0][0] == b"AB"
    assert chunks[0][1] is True
