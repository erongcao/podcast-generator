"""Tests for doubao_audio post-processing."""
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from doubao_audio import add_intro_outro, normalize_audio, cleanup_temp_files
from exceptions import AudioProcessingError


def test_normalize_audio_appends_suffix(tmp_path):
    inp = tmp_path / "input.mp3"
    inp.write_bytes(b"fake mp3")
    fake_result = MagicMock(returncode=0, stderr="")

    with patch("doubao_audio.subprocess.run", return_value=fake_result):
        out = normalize_audio(str(inp))
    assert out.endswith("_normalized.mp3")


def test_normalize_audio_no_double_suffix(tmp_path):
    inp = tmp_path / "already_normalized.mp3"
    inp.write_bytes(b"x")
    fake_result = MagicMock(returncode=0, stderr="")
    with patch("doubao_audio.subprocess.run", return_value=fake_result):
        out = normalize_audio(str(inp))
    # Should NOT become already_normalized_normalized.mp3
    assert "_normalized_normalized" not in out
    assert out == str(inp)  # Same path, no double suffix


def test_normalize_audio_ffmpeg_failure_raises(tmp_path):
    inp = tmp_path / "input.mp3"
    inp.write_bytes(b"x")
    fake_result = MagicMock(returncode=1, stderr="ffmpeg error")
    with patch("doubao_audio.subprocess.run", return_value=fake_result):
        with pytest.raises(AudioProcessingError, match="loudnorm"):
            normalize_audio(str(inp))


def test_normalize_audio_missing_ffmpeg_raises():
    with patch("doubao_audio.shutil.which", return_value=None):
        with pytest.raises(AudioProcessingError, match="ffmpeg not found"):
            normalize_audio("dummy.mp3")


def test_cleanup_temp_files_keeps_final(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "podcast_final.mp3").write_bytes(b"x")
    (tmp_path / "output" / "_0.mp3").write_bytes(b"x")  # chunk
    (tmp_path / "output" / "_0.txt").write_bytes(b"x")  # concat list
    (tmp_path / "output" / "intro_bgm.mp3").write_bytes(b"x")

    cleanup_temp_files("output/podcast_final.mp3")

    assert (tmp_path / "output" / "podcast_final.mp3").exists()
    assert not (tmp_path / "output" / "_0.mp3").exists()
    assert not (tmp_path / "output" / "_0.txt").exists()
    assert not (tmp_path / "output" / "intro_bgm.mp3").exists()


def test_add_intro_outro_no_intro_returns_main(tmp_path):
    main = tmp_path / "main.mp3"
    main.write_bytes(b"x")
    out = add_intro_outro(str(main))
    # No intro/outro supplied, just returns the main file
    assert out == str(main) or Path(out).exists()