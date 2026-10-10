#!/usr/bin/env python3
"""
麦克风录音 → WAV 文件（48kHz mono 16-bit）

采样率策略：
- 历史默认 16000 太低，Nyquist=8kHz 会砍掉所有齿音高频（s/sh/t 气声），
  DJI 拾音器等外接 mic 在 16kHz 下录出来明显发闷。
- 改为 48000：保留完整 mic 质量（DJI/Boya/罗技等外接 mic 原生支持 48kHz），
  后续 audio_cleanup 流程内部锁的就是 48kHz（见 audio_cleanup.py）。
- 48kHz 文件比 16kHz 大约 3 倍，30 秒 ref 大约 2.8 MB，可接受。
"""
import os
import tempfile
from pathlib import Path

import sounddevice as sd
import soundfile as sf

from exceptions import RecorderError

MIN_SECONDS = 10
MAX_SECONDS = 60
SAMPLE_RATE = 48000
CHANNELS = 1


def list_input_devices():
    """返回所有可用的输入设备名列表。"""
    devices = sd.query_devices()
    return [
        d["name"] for d in devices
        if isinstance(d, dict) and d.get("max_input_channels", 0) > 0
    ]


def record(seconds, output_path=None, sample_rate=SAMPLE_RATE):
    """
    录 seconds 秒到 WAV 文件。返回文件路径。
    seconds 必须在 [MIN_SECONDS, MAX_SECONDS] 范围。
    """
    if seconds < MIN_SECONDS:
        raise RecorderError(f"录音太短，至少 {MIN_SECONDS} 秒")
    if seconds > MAX_SECONDS:
        raise RecorderError(f"录音太长，最多 {MAX_SECONDS} 秒")
    if output_path is None:
        fd, output_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
    try:
        frames = sd.rec(
            int(seconds * sample_rate),
            samplerate=sample_rate,
            channels=CHANNELS,
            dtype="int16",
        )
        sd.wait()
        sf.write(output_path, frames, sample_rate, subtype="PCM_16")
        return output_path
    except Exception as e:
        if os.path.exists(output_path):
            os.unlink(output_path)
        raise RecorderError(f"录音失败: {e}") from e
