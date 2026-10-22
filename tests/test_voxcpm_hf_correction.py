"""
VoxCPM 高频滚降自动校正（方案 A，2026-10-22）

背景：VoxCPM2 会克隆参考音频的**音色**，但不克隆参考的**高频滚降**。实测
yao-1 前 12s：原录音滚降 19.9 dB、MiniMax 输出 21.4 dB、VoxCPM 只有 17.9 dB
—— 高频被 AudioVAE 解码抬高了约 5 dB，主观上就是"刺耳/齿音重"。这是
VoxCPM 和 MiniMax 最主要的质量差距。

两件事解决它：
  1. `_needs_resemble()` 从"仅 tilt>10 的男声"改成**默认全开**
     （原门控只管低频隆起，救不了"太亮"的音色）
  2. `_auto_high_shelf()` 按参考录音的滚降做**测量式**高架校正

这里锁住三件事：
  - 门控语义改成"默认开、no_enhance 才关"
  - 符号方向：rolloff 正值=暗，所以"太亮"是 got < ref（这个搞反过一次，
    测试就是为了钉死它）
  - 不越界：已经够暗的不动；天生亮的最多削 max_cut_db
"""
import shutil
import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="需要 ffmpeg"
)


# ============================================================
# 1. Resemble 门控语义
# ============================================================
def test_resemble_defaults_on():
    """默认开 —— 原门控只管低频，救不了 yao-1 这种偏亮的音色。"""
    from voxcpm_tts import _needs_resemble
    assert _needs_resemble(6.1, []) is True      # yao-1：以前永远不触发
    assert _needs_resemble(0.0, []) is True
    assert _needs_resemble(30.0, []) is True


def test_resemble_opt_out_tag():
    from voxcpm_tts import _needs_resemble
    assert _needs_resemble(6.1, ["no_enhance"]) is False


def test_female_tag_no_longer_disables():
    """
    'female' 曾经等于关闭，依据是"女声天然干净"。实测 yao-1 就是个偏亮的女声，
    恰恰需要处理 —— 所以这个语义被移除了，只留 no_enhance。
    """
    from voxcpm_tts import _needs_resemble
    assert _needs_resemble(6.1, ["female"]) is True
    # 但 no_enhance 仍然压过 female
    assert _needs_resemble(6.1, ["female", "no_enhance"]) is False


# ============================================================
# 2. 信号构造
# ============================================================
def _tone(tmp: Path, freq: int, amp_db: float) -> Path:
    out = tmp / f"t{freq}.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"sine=frequency={freq}:duration=2:sample_rate=48000",
         "-af", f"volume={amp_db}dB", str(out)],
        check=True, capture_output=True, timeout=60,
    )
    return out


def _mix(tmp: Path, name: str, parts: list[tuple[int, float]]) -> Path:
    """把若干正弦混成一路：(频率, 相对 dB)。"""
    args, labels = [], []
    for i, (f, a) in enumerate(parts):
        args += ["-i", str(_tone(tmp, f, a))]
        labels.append(f"[{i}:a]")
    fc = "".join(labels) + f"amix=inputs={len(parts)}:normalize=0[o]"
    out = tmp / f"{name}.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", *args, "-filter_complex", fc,
         "-map", "[o]", str(out)],
        check=True, capture_output=True, timeout=60,
    )
    return out


@pytest.fixture
def signals(tmp_path):
    """dark = 只有 200-2k 的基频；bright = 另加 9kHz 高频。"""
    return (
        _mix(tmp_path, "dark", [(500, 0)]),
        _mix(tmp_path, "bright", [(500, 0), (9000, -8)]),
    )


# ============================================================
# 3. _measure_rolloff
# ============================================================
def test_rolloff_direction(signals):
    """正值 = 暗。加了 9kHz 之后滚降必须变小（否则符号搞反了）。"""
    from voxcpm_tts import _measure_rolloff
    dark, bright = signals
    assert _measure_rolloff(dark) > _measure_rolloff(bright)


def test_rolloff_is_cached(signals, monkeypatch):
    """同一路径同一 mtime 只测一次（每个段都调，不缓存会拖慢合成）。"""
    import voxcpm_tts

    dark, _ = signals
    voxcpm_tts._rolloff_cache.clear()
    calls = []
    real = voxcpm_tts._band_rms_db

    def counting(*a, **k):
        calls.append(a)
        return real(*a, **k)

    monkeypatch.setattr(voxcpm_tts, "_band_rms_db", counting)
    voxcpm_tts._measure_rolloff(dark)
    voxcpm_tts._measure_rolloff(dark)
    assert len(calls) == 2, "第二次应命中缓存（一次测两个频段）"


# ============================================================
# 4. _auto_high_shelf
# ============================================================
def test_auto_shelf_noop_when_already_matching(signals, tmp_path):
    """已经和参考一样暗 → 一个 dB 都不动，输出照抄。"""
    from voxcpm_tts import _auto_high_shelf, _measure_rolloff

    dark, _ = signals
    ref = _measure_rolloff(dark)
    applied = _auto_high_shelf(dark, tmp_path / "out.wav", ref)

    assert applied == 0.0
    assert (tmp_path / "out.wav").exists(), "no-op 也要产出文件（后续步骤要读它）"
    assert _measure_rolloff(tmp_path / "out.wav") == pytest.approx(ref, abs=0.01)


def test_auto_shelf_cuts_when_too_bright(signals, tmp_path):
    """
    钉死符号方向：输出比参考**亮**（got < ref）必须削，
    而且要朝参考靠拢。搞反成 got > ref 的话这里会断言失败。
    """
    from voxcpm_tts import _auto_high_shelf, _measure_rolloff

    dark, bright = signals
    ref = _measure_rolloff(dark)
    before = _measure_rolloff(bright)
    assert before < ref, "前置条件：bright 比 dark 亮（滚降更小）"

    applied = _auto_high_shelf(bright, tmp_path / "out.wav", ref, max_cut_db=20.0)

    assert applied < 0, "太亮必须削（施加值应为负）"
    after = _measure_rolloff(tmp_path / "out.wav")
    assert after > before, f"削完要更暗：{before:.2f} → {after:.2f}"
    assert abs(after - ref) < abs(before - ref), "应朝参考靠拢"


def test_auto_shelf_respects_max_cut(signals, tmp_path):
    """天生亮到削不动的情况：最多削 max_cut_db，不能越界。"""
    from voxcpm_tts import _auto_high_shelf, _measure_rolloff

    dark, bright = signals
    ref = _measure_rolloff(dark)
    absurd_target = ref + 30.0  # 目标比任何实际音频都暗

    applied = _auto_high_shelf(bright, tmp_path / "out.wav", absurd_target, max_cut_db=2.0)

    assert applied >= -2.0 - 1e-6, f"不能超过 max_cut_db，实得 {applied}"
    assert _measure_rolloff(tmp_path / "out.wav") > _measure_rolloff(bright)


def test_auto_shelf_does_not_boost_dark_output(signals, tmp_path):
    """
    输出比参考还**暗**时不能反过来去提亮 —— 那是模型的音色特点，不是缺陷。
    （负 deficit 直接 no-op）
    """
    from voxcpm_tts import _auto_high_shelf, _measure_rolloff

    dark, _ = signals
    brighter_target = _measure_rolloff(dark) - 10.0  # 目标比 dark 亮

    applied = _auto_high_shelf(dark, tmp_path / "out.wav", brighter_target)

    assert applied == 0.0, "只削不推"
    assert _measure_rolloff(tmp_path / "out.wav") == pytest.approx(
        _measure_rolloff(dark), abs=0.01
    )