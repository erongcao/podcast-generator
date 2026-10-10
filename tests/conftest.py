"""Shared pytest fixtures."""
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def temp_voices_dir(monkeypatch):
    """Redirect voice_library to a temp dir for isolation."""
    tmp = Path(tempfile.mkdtemp(prefix="voices_test_"))
    monkeypatch.setattr("voice_library.VOICES_DIR", tmp)
    monkeypatch.setattr("voice_library.VOICES_JSON", tmp / "voices.json")
    monkeypatch.setattr("voice_library.AUDIO_DIR", tmp / "audio")
    return tmp


@pytest.fixture
def mock_sounddevice(monkeypatch):
    """Replace sounddevice with a fake that yields silence."""
    import numpy as np
    fake = MagicMock()
    fake.rec = MagicMock(return_value=np.zeros((16000, 1), dtype="int16"))
    fake.query_devices = MagicMock(return_value=[{"name": "MacBook Pro Mic", "max_input_channels": 1}])
    monkeypatch.setattr("voice_recorder.sd", fake)
    return fake


@pytest.fixture
def mock_requests_post(monkeypatch):
    """Replace requests.post with a configurable mock."""
    fake = MagicMock()
    monkeypatch.setattr("voice_cloner.requests.post", fake)
    return fake