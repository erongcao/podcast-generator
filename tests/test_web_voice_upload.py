"""
上传录音的格式解码回归测试（2026-10-21）

背景：上传路径以前直接 `shutil.copy2` 到 `{voice_id}.wav`，完全不管上传的
字节到底是什么容器。Safari 在 macOS 上录出来的是 `audio/mp4`（AAC），于是
一个 MP4 文件顶着 `.wav` 后缀进了音色库 —— 之后试听报错（Content-Type 说
audio/wav、负载是 MP4）、`wave.open()` 报 `file does not start with RIFF id`、
libsndfile 报 `Format not recognised`，VoxCPM2 / MiniMax clone 也都跑不起来。

这里锁住 `_decode_to_wav`：浏览器可能给的三种容器都必须转成真正的 PCM WAV，
解不出来的东西必须明确报错。
"""
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="需要 ffmpeg"
)


def _make_source(tmp: Path, codec_args: list, suffix: str) -> bytes:
    """用 ffmpeg 造一段 1s 测试音频，编码成目标容器，返回字节。"""
    out = tmp / f"src{suffix}"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         *codec_args, str(out)],
        check=True, capture_output=True, timeout=60,
    )
    return out.read_bytes()


def test_decode_mp4_aac_to_pcm_wav(tmp_path):
    """Safari 的 audio/mp4（AAC）→ 真正的 48kHz mono PCM WAV。"""
    data = _make_source(tmp_path, ["-c:a", "aac"], ".mp4")
    assert b"ftyp" in data[:64], "前置条件：这确实是 MP4 容器"

    dest = tmp_path / "out.wav"
    from gui.web.api.voices import _decode_to_wav
    _decode_to_wav(data, dest)

    with wave.open(str(dest)) as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2           # pcm_s16le
        assert w.getframerate() == 48000
        assert w.getnframes() == pytest.approx(48000, rel=0.05)


def test_decode_webm_opus_to_pcm_wav(tmp_path):
    """Chrome/Firefox 的 audio/webm（opus）→ 真正的 PCM WAV。"""
    data = _make_source(tmp_path, ["-c:a", "libopus"], ".webm")
    assert b"\x1a\x45\xdf\xa3" in data[:64], "前置条件：这确实是 WebM 容器"

    dest = tmp_path / "out.wav"
    from gui.web.api.voices import _decode_to_wav
    _decode_to_wav(data, dest)

    with wave.open(str(dest)) as w:
        assert w.getnchannels() == 1
        assert w.getframerate() == 48000


def test_decode_passthrough_real_wav(tmp_path):
    """本来就是合法 WAV 的上传（比如导入已有音色）不受影响。"""
    src = tmp_path / "real.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=1", str(src)],
        check=True, capture_output=True, timeout=60,
    )

    dest = tmp_path / "out.wav"
    from gui.web.api.voices import _decode_to_wav
    _decode_to_wav(src.read_bytes(), dest)

    with wave.open(str(dest)) as w:
        assert w.getframerate() == 48000
        assert w.getnchannels() == 1


def test_decode_rejects_garbage(tmp_path):
    """解不出来的字节必须抛 ValueError，而不是静默存成 .wav。"""
    from gui.web.api.voices import _decode_to_wav
    dest = tmp_path / "out.wav"
    with pytest.raises(ValueError):
        _decode_to_wav(b"this is definitely not audio, RIFF or not", dest)


def test_add_voice_impl_returns_400_and_leaves_no_file(tmp_path, monkeypatch):
    """
    端到端守住原始 bug：上传不可解码的音频 → 400，且不留半截文件在磁盘上。
    （解码发生在 SessionLocal() 之前，所以这个用例不需要 DB。）
    """
    from fastapi import HTTPException
    from gui.web.api import voices as voices_api

    monkeypatch.setattr(voices_api, "LEGACY_AUDIO_DIR", tmp_path)

    with pytest.raises(HTTPException) as exc:
        voices_api.add_voice_impl(
            voice_id="bad-audio",
            display_name="坏音频",
            file_bytes=b"RIFF\x00\x00\x00\x00WAVEfmt "[:-4],  # 截断的 RIFF
        )

    assert exc.value.status_code == 400
    assert "无法解码" in str(exc.value.detail)
    assert not (tmp_path / "bad-audio.wav").exists(), "失败时不该留下半截文件"