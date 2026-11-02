"""
VoxCPM per-segment loudnorm 降级路径（2026-10-26 事故）

事故链：
  段 12 的 loudnorm 真的失败（底层原因另查）→ 降级日志
  `f"per-segment loudnorm 失败 (seg {i}): ..."` 里的 `i` 是
  `synthesize_segments()` 的循环变量，在 `synthesize()` 作用域里不存在
  → NameError 把真实的 loudnorm 错误整个盖住，用户只看到
  "段 12 合成失败: name 'i' is not defined"。

  9c2c42a 只修了同一行的 `_log` 未定义（加了模块级 _log 兜底），
  没发现 `{i}` 也是 NameError —— f-string 先求值，_log 救不了它。

钉死的行为契约：loudnorm 失败 → 打警告、退回 raw wav、继续交付，
绝不抛 NameError。
"""
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="需要 ffmpeg"
)


class _FakeResult:
    """model.generate() 产物的最小替身：1 秒静音。"""

    def __init__(self):
        import numpy as np

        self.audio = np.zeros(24000, dtype=np.float32)
        self.sample_rate = 24000


class _FakeModel:
    def generate(self, **kwargs):
        yield _FakeResult()


@pytest.fixture
def ref_wav(tmp_path: Path) -> Path:
    ref = tmp_path / "ref.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=1:sample_rate=24000", str(ref)],
        check=True, capture_output=True, timeout=60,
    )
    return ref


def _raise(msg):
    def _inner(*a, **k):
        raise RuntimeError(msg)

    return _inner


def test_loudnorm_failure_falls_back_instead_of_nameerror(ref_wav, monkeypatch):
    """loudnorm 抛错时 synthesize() 必须退回 raw wav 交付 mp3，
    不能在降级日志里再炸一个 NameError 把真错误盖掉。"""
    import voxcpm_tts

    monkeypatch.setattr(voxcpm_tts, "_load_model", lambda: _FakeModel())
    monkeypatch.setattr(
        voxcpm_tts, "_loudnorm_two_pass", _raise("loudnorm 炸了（模拟段 12）")
    )
    # EQ 是 loudnorm 之后的下一个 best-effort 环节，一并打炸保持测试聚焦
    monkeypatch.setattr(voxcpm_tts, "_auto_high_shelf", _raise("eq 也炸"))

    mp3 = voxcpm_tts.synthesize(text="测试一下降级路径", wav_path=str(ref_wav))

    assert isinstance(mp3, bytes) and len(mp3) > 0, "loudnorm 失败也要交付音频"
