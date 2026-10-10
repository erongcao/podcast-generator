"""Tests for the async long-text TTS module (doubao_async_tts.py).

These tests use mocks for HTTP — no real API calls are made.
For an integration probe see scripts/probe_async.py in the repo (not committed).
"""
import io
import json
import tarfile
from unittest.mock import MagicMock, patch

import pytest
import requests

import doubao_async_tts as at_mod


def make_response(status_code=200, json_data=None, content=b"", raise_json_error=False):
    """Build a MagicMock that quacks like a requests.Response."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    if content:
        resp.content = content
    if raise_json_error:
        resp.json.side_effect = ValueError("bad json")
    elif json_data is not None:
        resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


def make_tar_bytes(mp3_bytes=b"fake-mp3-bytes", titles=None, extra=None) -> bytes:
    """Build a real tar archive containing one .mp3, .titles, .extra entry."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        # mp3
        mp3_bytes_io = io.BytesIO(mp3_bytes)
        info = tarfile.TarInfo(name="content-abc.mp3")
        info.size = len(mp3_bytes)
        tar.addfile(info, mp3_bytes_io)
        # titles
        titles_bytes = json.dumps(titles or []).encode("utf-8")
        info = tarfile.TarInfo(name="content-abc.titles")
        info.size = len(titles_bytes)
        tar.addfile(info, io.BytesIO(titles_bytes))
        # extra
        extra_bytes = json.dumps(extra or {}).encode("utf-8")
        info = tarfile.TarInfo(name="content-abc.extra")
        info.size = len(extra_bytes)
        tar.addfile(info, io.BytesIO(extra_bytes))
    return buf.getvalue()


# ============ submit_long_text ============

def test_submit_short_text_uses_text_field():
    """Texts under 50K go directly in the 'text' field (no upload)."""
    with patch("doubao_async_tts._post_submit") as mock_submit, \
         patch("doubao_async_tts._post_upload") as mock_upload:
        mock_submit.return_value = make_response(200, {
            "task_id": 123, "file_id": 456, "task_token": "tok",
            "base_resp": {"status_code": 0},
        })
        result = at_mod.submit_long_text(
            text="短文本测试",
            voice_id="male-qn-qingse",
            api_key="sk-test",
        )
    assert result["task_id"] == 123
    assert result["file_id"] == 456
    # upload should NOT have been called
    mock_upload.assert_not_called()
    # verify payload used "text", not "text_file_id"
    payload = mock_submit.call_args.args[0]
    assert payload["text"] == "短文本测试"
    assert "text_file_id" not in payload
    assert payload["voice_setting"]["voice_id"] == "male-qn-qingse"


def test_submit_long_text_uploads_file_first():
    """Texts over 50K go through the file-upload path."""
    long_text = "x" * 60_000

    with patch("doubao_async_tts._post_upload") as mock_upload, \
         patch("doubao_async_tts._post_submit") as mock_submit:
        mock_upload.return_value = make_response(200, {
            "file": {"file_id": "uploaded123"},
            "base_resp": {"status_code": 0},
        })
        mock_submit.return_value = make_response(200, {
            "task_id": "task999", "file_id": "file999",
            "task_token": "tok999",
            "base_resp": {"status_code": 0},
        })
        result = at_mod.submit_long_text(
            text=long_text,
            voice_id="male-qn-qingse",
            api_key="sk-test",
        )

    # upload was called once with text content
    mock_upload.assert_called_once()
    upload_args = mock_upload.call_args.args
    assert upload_args[0] == long_text.encode("utf-8")

    # verify payload used text_file_id, not text
    payload = mock_submit.call_args.args[0]
    assert payload["text_file_id"] == "uploaded123"
    assert "text" not in payload

    assert result["task_id"] == "task999"


def test_submit_passes_audio_and_voice_settings():
    """Verify voice_setting and audio_setting get propagated."""
    with patch("doubao_async_tts._post_submit") as mock_submit:
        mock_submit.return_value = make_response(200, {
            "task_id": 1, "file_id": 1, "task_token": "t",
            "base_resp": {"status_code": 0},
        })
        at_mod.submit_long_text(
            text="hi",
            voice_id="v1",
            api_key="sk",
            speed=1.5, vol=2.0, pitch=3,
            emotion="happy",
            audio_format="wav", sample_rate=24000, bitrate=64000, channel=2,
            language_boost="English",
            subtitle_enable=True,
            subtitle_type="word",
        )
    payload = mock_submit.call_args.args[0]
    assert payload["voice_setting"]["speed"] == 1.5
    assert payload["voice_setting"]["vol"] == 2.0
    assert payload["voice_setting"]["pitch"] == 3
    assert payload["voice_setting"]["emotion"] == "happy"
    assert payload["audio_setting"]["format"] == "wav"
    assert payload["audio_setting"]["sample_rate"] == 24000
    assert payload["language_boost"] == "English"
    assert payload["subtitle_enable"] is True
    assert payload["subtitle_type"] == "word"


def test_submit_failure_raises_api_error():
    """Submit failure (base_resp.status_code != 0) raises APIError."""
    from exceptions import APIError
    with patch("doubao_async_tts._post_submit") as mock_submit:
        mock_submit.return_value = make_response(200, {
            "base_resp": {"status_code": 1, "status_msg": "rate limited"},
            "trace_id": "abc123",
        })
        with pytest.raises(APIError) as exc_info:
            at_mod.submit_long_text(text="x", voice_id="v", api_key="sk")
    assert "rate limited" in str(exc_info.value)
    assert exc_info.value.trace_id == "abc123"


def test_submit_invalid_json_raises():
    """Non-JSON submit response is wrapped in APIError."""
    from exceptions import APIError
    with patch("doubao_async_tts._post_submit") as mock_submit:
        mock_submit.return_value = make_response(200, raise_json_error=True)
        with pytest.raises(APIError) as exc_info:
            at_mod.submit_long_text(text="x", voice_id="v", api_key="sk")
    assert "non-JSON" in str(exc_info.value)


def test_submit_upload_missing_file_id_raises():
    """If upload returns no file_id, submit raises APIError."""
    from exceptions import APIError
    long_text = "y" * 60_000
    with patch("doubao_async_tts._post_upload") as mock_upload:
        mock_upload.return_value = make_response(200, {"file": {}, "base_resp": {"status_code": 0}})
        with pytest.raises(APIError):
            at_mod.submit_long_text(text=long_text, voice_id="v", api_key="sk")


# ============ poll_for_completion ============

def test_poll_returns_file_meta_on_success():
    """Poll returns file metadata when status becomes ready."""
    responses = [
        make_response(200, {"file": None, "base_resp": {"status_code": 2013}}),
        make_response(200, {"file": None, "base_resp": {"status_code": 2013}}),
        make_response(200, {
            "file": {"file_id": 99, "bytes": 12345,
                     "download_url": "https://oss.example.com/abc.tar?sig=xyz"},
            "base_resp": {"status_code": 0},
        }),
    ]
    with patch("doubao_async_tts._get_file_meta", side_effect=responses), \
         patch("time.sleep"):
        meta = at_mod.poll_for_completion("task1", "sk", timeout=30, poll_interval=0)
    assert meta["file_id"] == 99
    assert "download_url" in meta


def test_poll_uses_explicit_file_id():
    """If file_id is explicitly passed, use it for /v1/files/retrieve."""
    with patch("doubao_async_tts._get_file_meta") as mock_get, \
         patch("time.sleep"):
        mock_get.return_value = make_response(200, {
            "file": {"file_id": "explicit_fid", "download_url": "u"},
            "base_resp": {"status_code": 0},
        })
        at_mod.poll_for_completion(
            task_id="taskA",
            api_key="sk",
            file_id="explicit_fid",
            timeout=10,
            poll_interval=0,
        )
    # file_id (1st positional arg) should be the explicit one, not taskA
    called_file_id = mock_get.call_args.args[0]
    assert called_file_id == "explicit_fid"


def test_poll_falls_back_to_task_id():
    """If file_id not passed, the task_id is used for retrieval."""
    with patch("doubao_async_tts._get_file_meta") as mock_get, \
         patch("time.sleep"):
        mock_get.return_value = make_response(200, {
            "file": {"file_id": 42, "download_url": "u"},
            "base_resp": {"status_code": 0},
        })
        at_mod.poll_for_completion(task_id=42, api_key="sk", timeout=10, poll_interval=0)
    called_file_id = mock_get.call_args.args[0]
    assert called_file_id == 42


def test_poll_unknown_error_raises():
    """Unexpected base_resp.status_code (not 2013, not 0) raises APIError."""
    from exceptions import APIError
    with patch("doubao_async_tts._get_file_meta") as mock_get, \
         patch("time.sleep"):
        mock_get.return_value = make_response(200, {
            "file": None,
            "base_resp": {"status_code": 9999, "status_msg": "weird"},
        })
        with pytest.raises(APIError):
            at_mod.poll_for_completion("t1", "sk", timeout=10, poll_interval=0)


def test_poll_times_out():
    """If task never completes, TimeoutError is raised."""
    with patch("doubao_async_tts._get_file_meta") as mock_get, \
         patch("time.sleep"):
        mock_get.return_value = make_response(200, {"file": None, "base_resp": {"status_code": 2013}})
        with pytest.raises(TimeoutError):
            at_mod.poll_for_completion("t1", "sk", timeout=0.1, poll_interval=0)


def test_poll_treats_missing_download_url_as_pending():
    """file is present but download_url missing → still pending."""
    with patch("doubao_async_tts._get_file_meta") as mock_get, \
         patch("time.sleep"):
        # 第一次没 download_url，第二次有了
        mock_get.side_effect = [
            make_response(200, {
                "file": {"file_id": 1, "bytes": 0},  # no download_url
                "base_resp": {"status_code": 0},
            }),
            make_response(200, {
                "file": {"file_id": 1, "download_url": "https://x/y.tar"},
                "base_resp": {"status_code": 0},
            }),
        ]
        meta = at_mod.poll_for_completion("t1", "sk", timeout=10, poll_interval=0)
    assert meta["download_url"] == "https://x/y.tar"


# ============ download / extract ============

def test_download_audio_returns_content():
    """download_audio returns raw response content."""
    with patch("doubao_async_tts._get_file_bytes") as mock_get:
        mock_get.return_value = make_response(200, content=b"abc123")
        result = at_mod.download_audio("f1", "sk")
    assert result == b"abc123"


def test_download_audio_from_url_no_auth():
    """download_audio_from_url uses the OSS URL without Authorization."""
    with patch("doubao_async_tts._download_url") as mock_dl:
        mock_dl.return_value = make_response(200, content=b"raw-bytes")
        result = at_mod.download_audio_from_url("https://oss.example.com/x.tar?sig=abc")
    assert result == b"raw-bytes"
    # Should NOT have Authorization header
    kwargs = mock_dl.call_args.kwargs
    assert "headers" not in kwargs or "Authorization" not in kwargs.get("headers", {})


def test_extract_tar_decodes_mp3_titles_extra():
    """extract_tar returns the three expected fields."""
    tar_bytes = make_tar_bytes(
        mp3_bytes=b"hello-mp3",
        titles=[{"text": "你好", "time_begin": 0, "time_end": 1000}],
        extra={"audio_length": 1000, "audio_sample_rate": 32000},
    )
    out = at_mod.extract_tar(tar_bytes)
    assert out["mp3"] == b"hello-mp3"
    assert out["titles"][0]["text"] == "你好"
    assert out["extra"]["audio_length"] == 1000


def test_extract_tar_handles_missing_fields():
    """Missing files → corresponding key is None, no crash."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name="only-extra.extra")
        body = json.dumps({"audio_length": 1}).encode()
        info.size = len(body)
        tar.addfile(info, io.BytesIO(body))
    out = at_mod.extract_tar(buf.getvalue())
    assert out["mp3"] is None
    assert out["titles"] is None
    assert out["extra"]["audio_length"] == 1


def test_extract_tar_handles_malformed_titles_json():
    """Bad JSON in .titles is tolerated (None + warning)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name="x.titles")
        body = b"not json"
        info.size = len(body)
        tar.addfile(info, io.BytesIO(body))
    out = at_mod.extract_tar(buf.getvalue())
    assert out["titles"] is None


# ============ synthesize_long_text (high-level) ============

def test_synthesize_long_text_full_flow_uses_oss_url():
    """End-to-end: submit → poll → download via OSS URL → extract tar."""
    tar_bytes = make_tar_bytes(mp3_bytes=b"end-mp3")

    fake_meta = {"file": {"file_id": 1, "download_url": "https://oss.example.com/x.tar?sig=1"}}
    with patch("doubao_async_tts.submit_long_text") as mock_submit, \
         patch("doubao_async_tts.poll_for_completion") as mock_poll, \
         patch("doubao_async_tts.download_audio_from_url") as mock_dl_url, \
         patch("doubao_async_tts.download_audio") as mock_dl:
        mock_submit.return_value = {"task_id": 1, "file_id": 1, "task_token": "t"}
        mock_poll.return_value = fake_meta["file"]
        mock_dl_url.return_value = tar_bytes

        out = at_mod.synthesize_long_text(
            text="一段长文本测试",
            voice_id="male-qn-qingse",
            api_key="sk-test",
        )

    mock_submit.assert_called_once()
    mock_poll.assert_called_once()
    mock_dl_url.assert_called_once_with("https://oss.example.com/x.tar?sig=1")
    mock_dl.assert_not_called()  # didn't need to fall back
    assert out["mp3"] == b"end-mp3"


def test_synthesize_long_text_falls_back_to_file_id_download():
    """If no download_url in file metadata, use /v1/files/retrieve_content."""
    tar_bytes = make_tar_bytes(mp3_bytes=b"fallback-mp3")
    with patch("doubao_async_tts.submit_long_text") as mock_submit, \
         patch("doubao_async_tts.poll_for_completion") as mock_poll, \
         patch("doubao_async_tts.download_audio_from_url") as mock_dl_url, \
         patch("doubao_async_tts.download_audio") as mock_dl:
        mock_submit.return_value = {"task_id": 9, "file_id": 9, "task_token": "t"}
        mock_poll.return_value = {"file_id": 9}  # no download_url
        mock_dl.return_value = tar_bytes

        out = at_mod.synthesize_long_text(text="hi", voice_id="v", api_key="sk")

    mock_dl_url.assert_not_called()
    mock_dl.assert_called_once_with(9, "sk")
    assert out["mp3"] == b"fallback-mp3"


# ============ constants ============

def test_text_direct_limit_is_50k():
    """Sanity: 50K is the documented async text param limit."""
    assert at_mod.TEXT_DIRECT_LIMIT == 50_000