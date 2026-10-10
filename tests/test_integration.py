"""End-to-end test: clone a voice, pick from library, synthesize with it."""
from unittest.mock import MagicMock, patch
import pytest

import voice_library as vl
import voice_cloner as vc
from exceptions import LibraryError


def test_clone_then_pick_then_synthesize(temp_voices_dir, tmp_path, mock_requests_post):
    """All externals mocked. Verifies the glue between modules."""
    # Setup: an external WAV to "clone"
    wav = tmp_path / "sample.wav"
    wav.write_bytes(b"fake wav bytes")

    # Mock two-step clone API: upload → file_id, then clone → success
    upload_resp = MagicMock()
    upload_resp.status_code = 200
    upload_resp.text = ""
    upload_resp.json = MagicMock(return_value={
        "file": {"file_id": 431290713448736},
        "base_resp": {"status_code": 0},
    })
    clone_resp = MagicMock()
    clone_resp.status_code = 200
    clone_resp.text = ""
    clone_resp.json = MagicMock(return_value={
        "base_resp": {"status_code": 0},
        "voice_id": "doctor_voice",
    })
    mock_requests_post.side_effect = [upload_resp, clone_resp]

    # 1. Clone (no sample_text in new API)
    voice_id = vc.clone(str(wav), voice_id="doctor_voice", api_key="sk-test")
    assert voice_id == "doctor_voice"

    # 2. Add to library
    vl.add(voice_id, "医生声音", str(wav), notes="测试用")

    # 3. List and pick
    library = vl.list_all()
    assert any(v["voice_id"] == "doctor_voice" for v in library)

    # 4. Synthesize a test phrase with the new voice
    with patch("voice_cloner.minimax_tts.synthesize", return_value=b"mp3") as mock_synth:
        result = vc.synthesize_test("doctor_voice", "你好", api_key="sk-test")
    assert result == b"mp3"
    mock_synth.assert_called_once()
    call_kwargs = mock_synth.call_args.kwargs
    assert call_kwargs["voice_id"] == "doctor_voice"


def test_duplicate_voice_id_rejected(temp_voices_dir, tmp_path):
    """Adding the same voice_id twice should raise LibraryError."""
    vl.init()
    wav = tmp_path / "x.wav"
    wav.write_bytes(b"x")
    vl.add("dup", "甲", str(wav))
    with pytest.raises(LibraryError, match="已存在"):
        vl.add("dup", "乙", str(wav))


def test_delete_then_re_add_works(temp_voices_dir, tmp_path):
    """After delete, the same voice_id can be re-added."""
    vl.init()
    wav = tmp_path / "x.wav"
    wav.write_bytes(b"x")
    vl.add("re", "甲", str(wav))
    vl.delete("re")
    vl.add("re", "乙", str(wav))  # should not raise
    assert vl.exists("re")
