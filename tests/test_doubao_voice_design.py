"""Tests for the doubao_voice_design module (text-to-voice)."""
from unittest.mock import MagicMock, patch

import pytest
import requests

import doubao_voice_design as dvd


def make_response(status_code=200, json_data=None, raise_json_error=False):
    """Build a MagicMock that quacks like a requests.Response."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    if raise_json_error:
        resp.json.side_effect = ValueError("bad json")
    elif json_data is not None:
        resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    resp.text = json_data and str(json_data) or ""
    return resp


# ============ design_voice ============

def test_design_voice_returns_voice_id_and_preview_audio():
    """Successful design returns voice_id and hex-decoded preview audio bytes."""
    fake_hex = "494433" * 8  # "ID3" header repeated (24 raw bytes)
    with patch("doubao_voice_design._post_design_request") as mock_post:
        mock_post.return_value = make_response(200, {
            "voice_id": "designed_voice_123",
            "trial_audio": fake_hex,
            "extra_info": {"usage_characters": 16, "charge_method": "voice_design"},
            "base_resp": {"status_code": 0, "status_msg": "success"},
        })
        result = dvd.design_voice(
            prompt="中年男医生",
            api_key="sk-test",
        )
    assert result["voice_id"] == "designed_voice_123"
    assert result["preview_audio"] == bytes.fromhex(fake_hex)
    assert result["preview_audio"].startswith(b"ID3")
    assert result["extra_info"]["charge_method"] == "voice_design"


def test_design_voice_passes_payload_correctly():
    """Verify prompt, gender, language, voice_id all reach the API payload."""
    with patch("doubao_voice_design._post_design_request") as mock_post:
        mock_post.return_value = make_response(200, {
            "voice_id": "auto_id_xyz",
            "trial_audio": "",
            "base_resp": {"status_code": 0, "status_msg": "success"},
        })
        dvd.design_voice(
            prompt="温柔的女声",
            api_key="sk-test",
            gender="female",
            language="Chinese",
            preview_text="你好，世界",
            voice_id="my_custom_v1",
        )
    kwargs = mock_post.call_args.kwargs
    assert kwargs["headers"]["Authorization"] == "Bearer sk-test"
    json_body = kwargs["json_body"]
    assert json_body["prompt"] == "温柔的女声"
    assert json_body["gender"] == "female"
    assert json_body["language"] == "Chinese"
    assert json_body["preview_text"] == "你好，世界"
    assert json_body["voice_id"] == "my_custom_v1"


def test_design_voice_omits_voice_id_when_not_provided():
    """Without voice_id kwarg, the field is NOT included in the payload."""
    with patch("doubao_voice_design._post_design_request") as mock_post:
        mock_post.return_value = make_response(200, {
            "voice_id": "auto_generated",
            "trial_audio": "",
            "base_resp": {"status_code": 0, "status_msg": "success"},
        })
        dvd.design_voice(prompt="x", api_key="sk")
    kwargs = mock_post.call_args.kwargs
    json_body = kwargs["json_body"]
    assert "voice_id" not in json_body


def test_design_voice_api_error_raises():
    """Non-zero base_resp.status_code raises APIError."""
    from exceptions import APIError
    with patch("doubao_voice_design._post_design_request") as mock_post:
        mock_post.return_value = make_response(200, {
            "base_resp": {"status_code": 1, "status_msg": "bad prompt"},
            "trace_id": "trace123",
        })
        with pytest.raises(APIError) as exc_info:
            dvd.design_voice("x", "sk")
    assert "bad prompt" in str(exc_info.value)
    assert exc_info.value.trace_id == "trace123"


def test_design_voice_http_error_raises():
    """HTTP non-200 raises APIError."""
    from exceptions import APIError
    with patch("doubao_voice_design._post_design_request") as mock_post:
        mock_post.return_value = make_response(404, json_data={"error": "not found"})
        with pytest.raises(APIError) as exc_info:
            dvd.design_voice("x", "sk")
    assert "404" in str(exc_info.value)


def test_design_voice_invalid_json_raises():
    """Non-JSON response is wrapped in APIError."""
    from exceptions import APIError
    with patch("doubao_voice_design._post_design_request") as mock_post:
        mock_post.return_value = make_response(200, raise_json_error=True)
        with pytest.raises(APIError) as exc_info:
            dvd.design_voice("x", "sk")
    assert "invalid JSON" in str(exc_info.value)


def test_design_voice_missing_voice_id_raises():
    """Response with no voice_id raises APIError."""
    from exceptions import APIError
    with patch("doubao_voice_design._post_design_request") as mock_post:
        mock_post.return_value = make_response(200, {
            "trial_audio": "",
            "base_resp": {"status_code": 0, "status_msg": "success"},
        })
        with pytest.raises(APIError) as exc_info:
            dvd.design_voice("x", "sk")
    assert "missing voice_id" in str(exc_info.value)


def test_design_voice_bad_trial_audio_hex_raises():
    """Non-hex trial_audio raises APIError."""
    from exceptions import APIError
    with patch("doubao_voice_design._post_design_request") as mock_post:
        mock_post.return_value = make_response(200, {
            "voice_id": "v",
            "trial_audio": "NOT_HEX_!!!",
            "base_resp": {"status_code": 0, "status_msg": "success"},
        })
        with pytest.raises(APIError) as exc_info:
            dvd.design_voice("x", "sk")
    assert "bad trial_audio" in str(exc_info.value)


def test_design_url_is_correct():
    """Sanity: URL points to /v1/voice_design."""
    assert dvd.DESIGN_URL == "https://api.minimaxi.com/v1/voice_design"