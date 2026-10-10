"""Tests for voice_cloner module (HTTP mocked).

The MiniMax /v1/voice_clone endpoint requires a two-step flow:
  1. POST /v1/files/upload  (multipart)  → returns {"file": {"file_id": <int>}}
  2. POST /v1/voice_clone   (JSON)       → body: {"file_id": <int>, "voice_id": <str>}
"""
from unittest.mock import MagicMock, patch

import pytest

import voice_cloner as vc
from exceptions import VoiceCloneError


def _mock_response(status_code=200, json_payload=None):
    """Build a mock requests.Response-like object."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = "" if json_payload is None else str(json_payload)
    resp.json = MagicMock(return_value=json_payload if json_payload is not None else {})
    return resp


def test_clone_success_returns_voice_id(mock_requests_post, tmp_path):
    """Happy path: upload returns file_id, clone returns success → returns voice_id."""
    upload_resp = _mock_response(
        status_code=200,
        json_payload={
            "file": {"file_id": 431290713448736, "bytes": 1024},
            "base_resp": {"status_code": 0, "status_msg": "success"},
        },
    )
    clone_resp = _mock_response(
        status_code=200,
        json_payload={
            "base_resp": {"status_code": 0, "status_msg": "success"},
            "voice_id": "myvoiceid123",
        },
    )
    mock_requests_post.side_effect = [upload_resp, clone_resp]

    wav = tmp_path / "in.wav"
    wav.write_bytes(b"fake wav bytes")
    result = vc.clone(wav_path=str(wav), voice_id="myvoiceid123", api_key="sk-test")
    assert result == "myvoiceid123"


def test_clone_two_step_calls_upload_first(mock_requests_post, tmp_path):
    """The upload endpoint MUST be called before the clone endpoint."""
    upload_resp = _mock_response(
        status_code=200,
        json_payload={
            "file": {"file_id": 999},
            "base_resp": {"status_code": 0},
        },
    )
    clone_resp = _mock_response(
        status_code=200,
        json_payload={"base_resp": {"status_code": 0}},
    )
    mock_requests_post.side_effect = [upload_resp, clone_resp]

    wav = tmp_path / "in.wav"
    wav.write_bytes(b"x")
    vc.clone(str(wav), voice_id="vid_abcd", api_key="sk-test")

    assert mock_requests_post.call_count == 2
    first_call = mock_requests_post.call_args_list[0]
    second_call = mock_requests_post.call_args_list[1]

    # Step 1: upload URL must contain /v1/files/upload
    first_url = first_call.args[0] if first_call.args else first_call.kwargs.get("url", "")
    assert "/v1/files/upload" in str(first_url), f"First call should hit upload endpoint, got: {first_url}"

    # Step 2: clone URL must contain /v1/voice_clone
    second_url = second_call.args[0] if second_call.args else second_call.kwargs.get("url", "")
    assert "/v1/voice_clone" in str(second_url), f"Second call should hit clone endpoint, got: {second_url}"


def test_clone_uses_integer_file_id(mock_requests_post, tmp_path):
    """The JSON body sent to /v1/voice_clone MUST contain file_id as a NUMBER (not string)."""
    upload_resp = _mock_response(
        status_code=200,
        json_payload={
            "file": {"file_id": 431290713448736},
            "base_resp": {"status_code": 0},
        },
    )
    clone_resp = _mock_response(
        status_code=200,
        json_payload={"base_resp": {"status_code": 0}},
    )
    mock_requests_post.side_effect = [upload_resp, clone_resp]

    wav = tmp_path / "in.wav"
    wav.write_bytes(b"x")
    vc.clone(str(wav), voice_id="vid_integer", api_key="sk-test")

    # Find the clone call (the JSON one) and inspect its body
    clone_call = None
    for call in mock_requests_post.call_args_list:
        url = call.args[0] if call.args else call.kwargs.get("url", "")
        if "/v1/voice_clone" in str(url):
            clone_call = call
            break

    assert clone_call is not None, "Clone endpoint was never called"
    body = clone_call.kwargs.get("json")
    assert body is not None, "Clone call must use json= kwarg, not data="
    assert "file_id" in body, f"Clone body must contain file_id, got: {body}"
    file_id_value = body["file_id"]
    assert isinstance(file_id_value, int), (
        f"file_id must be a Python int (so requests serializes as JSON number), "
        f"got {type(file_id_value).__name__}: {file_id_value!r}"
    )
    assert file_id_value == 431290713448736


def test_clone_raises_on_upload_failure(mock_requests_post, tmp_path):
    """If upload returns non-200, raise VoiceCloneError."""
    upload_resp = _mock_response(
        status_code=403,
        json_payload={"base_resp": {"status_code": 1004, "status_msg": "鉴权失败"}},
    )
    upload_resp.text = "forbidden"
    mock_requests_post.side_effect = [upload_resp]

    wav = tmp_path / "in.wav"
    wav.write_bytes(b"x")
    with pytest.raises(VoiceCloneError, match="Upload"):
        vc.clone(str(wav), voice_id="vid_upload_fail", api_key="sk-test")


def test_clone_raises_on_api_error_status(mock_requests_post, tmp_path):
    """If clone returns base_resp.status_code != 0, raise VoiceCloneError."""
    upload_resp = _mock_response(
        status_code=200,
        json_payload={
            "file": {"file_id": 12345},
            "base_resp": {"status_code": 0},
        },
    )
    clone_resp = _mock_response(
        status_code=200,
        json_payload={
            "base_resp": {"status_code": 2013, "status_msg": "invalid params"},
        },
    )
    mock_requests_post.side_effect = [upload_resp, clone_resp]

    wav = tmp_path / "in.wav"
    wav.write_bytes(b"x")
    with pytest.raises(VoiceCloneError, match="2013|invalid params|Clone"):
        vc.clone(str(wav), voice_id="vid_api_err", api_key="sk-test")


def test_clone_429_raises_with_status(mock_requests_post, tmp_path):
    """429 from clone endpoint → VoiceCloneError with status_code=429."""
    upload_resp = _mock_response(
        status_code=200,
        json_payload={
            "file": {"file_id": 111},
            "base_resp": {"status_code": 0},
        },
    )
    clone_resp = _mock_response(
        status_code=429,
        json_payload={"base_resp": {"status_code": 1002, "status_msg": "限流"}},
    )
    clone_resp.text = "rate limited"
    # Retry decorator makes 3 attempts on 429; provide 3 responses for the
    # clone call (plus the upload response) so the retry exhausts.
    mock_requests_post.side_effect = [
        upload_resp,
        clone_resp, clone_resp, clone_resp,
    ]

    wav = tmp_path / "in.wav"
    wav.write_bytes(b"x")
    with pytest.raises(VoiceCloneError) as exc:
        vc.clone(str(wav), voice_id="vid_429", api_key="sk-test")
    assert exc.value.status_code == 429


def test_synthesize_test_returns_bytes(mock_requests_post):
    """synthesize_test delegates to existing minimax_tts.synthesize — does NOT call clone endpoint."""
    import minimax_tts

    with patch.object(minimax_tts, "synthesize", return_value=b"mp3bytes") as mock_synth:
        result = vc.synthesize_test(voice_id="v1", text="hi", api_key="sk-test")
    assert result == b"mp3bytes"
    mock_synth.assert_called_once()
    mock_requests_post.assert_not_called()
