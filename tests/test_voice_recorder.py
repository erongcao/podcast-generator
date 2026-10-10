"""Tests for voice_recorder module."""
import os
import wave
from pathlib import Path
import pytest

import voice_recorder as vr
from exceptions import RecorderError


def test_record_produces_valid_wav(mock_sounddevice, tmp_path):
    out = vr.record(seconds=15, output_path=str(tmp_path / "out.wav"))
    assert Path(out).exists()
    with wave.open(out, "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 48000


def test_record_enforces_min_duration():
    with pytest.raises(RecorderError, match="10 秒"):
        vr.record(seconds=5)


def test_record_enforces_max_duration():
    with pytest.raises(RecorderError, match="60 秒"):
        vr.record(seconds=120)


def test_record_cleans_temp_on_error(mock_sounddevice, monkeypatch, tmp_path):
    """如果 sounddevice 抛错，临时文件应被删除。"""
    mock_sounddevice.rec.side_effect = RuntimeError("mic busy")
    out = str(tmp_path / "out.wav")
    with pytest.raises(RecorderError):
        vr.record(seconds=15, output_path=out)
    assert not Path(out).exists()


def test_list_devices_returns_list(mock_sounddevice):
    mock_sounddevice.query_devices.return_value = [{"name": "MacBook Pro Mic"}]
    devices = vr.list_input_devices()
    assert isinstance(devices, list)
