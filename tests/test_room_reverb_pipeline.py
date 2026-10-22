"""演播室混响接进 voxcpm 流水线后的验收。

坑记录（都踩过，别改回去）：
  1. **混响必须放在 concat 之后整段做，不能逐段做**。段 mp3 是首尾硬接的，
     中间没有静音 —— 逐段加混响时段 N 的余响会盖住段 N+1 的开头，每个段边界
     都出现一次可听的「缝」。整段做，段 N 的衰减自然填进段 N+1 的起始。
     `test_reverb_runs_once_on_the_concat` 直接守住这条。
  2. **必须放在 Resemble 之后**。Resemble 是去混响的，先加的混响会被减掉，
     等于白加。（链路：模型 → Resemble → per-seg loudnorm → HF shelf → concat）
  3. **末尾必须再 loudnorm 一次**。per-segment loudnorm 已经做到 -16 LUFS，
     但扩散场叠在干声上会整体抬电平和峰值，不收尾成品会超 -16。
  4. **C50 用 350 ms 分帧取中位数，且只取发声帧**。整段算会被长停顿污染（干声
     能算出 -36 dB 这种无意义值）；不丢静音帧则中位数帧会落在停顿上，量的
     是「混响往空隙里填了多少尾巴」而不是清晰度，虚高到 +2.98 dB（真值 -0.33）。
     ΔC50 小于 ±2 dB = 清晰度人耳听不出。
  5. **量「有没有真的变湿」必须用 normalize=False**。成品归一化按 RMS，
     会把湿/干能量差一起拉平（那正是要测的量）。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import voxcpm_tts  # noqa: E402
from room_ir import convolve_verb, studio_ir  # noqa: E402

SR = 48000
pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg"), reason="需要 ffmpeg")


# ---------------------------------------------------------------- 音频工具


def _speechlike(seconds: float = 3.0, sr: int = SR) -> np.ndarray:
    """类语音测试信号：带停顿的谐波串 + 缓慢音高包络。

    不能用纯正弦 —— 正弦的 ebur128 块级门限判定很不稳定，而且没有停顿就没有
    「余响填进下一句」可测。也不能用白噪 —— 白噪没有谐波结构，测不出清晰度。
    """
    rng = np.random.default_rng(0)
    x = np.zeros(int(seconds * sr), dtype=np.float64)
    t = np.arange(len(x)) / sr
    # 5 句，每句 0.35 s 发声 + 0.25 s 停顿
    pos, dur = 0.0, 0.35
    while pos + dur < seconds:
        i0 = int(pos * sr)
        f0 = 120 + 30 * (pos % 1.5)
        seg_t = t[i0:i0 + int(dur * sr)] - pos
        env = np.sin(np.pi * np.clip(seg_t / dur, 0, 1)) ** 0.7
        wave = sum(np.sin(2 * np.pi * f0 * k * seg_t) / k
                   for k in range(1, 12))
        x[i0:i0 + int(dur * sr)] += wave * env
        pos += 0.6
    x += rng.normal(0, 1e-4, len(x))     # 一丁点底噪，别让段落是完全静音
    return (x / np.abs(x).max() * 0.5).astype(np.float32)


def _to_mp3(x: np.ndarray, path: Path, sr: int = SR) -> Path:
    w = path.with_suffix(".wav")
    sf.write(str(w), x, sr, subtype="PCM_16")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(w),
                    "-c:a", "libmp3lame", "-b:a", "320k", "-ar", str(sr),
                    "-ac", "1", str(path)],
                   check=True, capture_output=True, timeout=300)
    return path


def _read48(p: Path, tmp: Path) -> np.ndarray:
    o = tmp / "r.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(p),
                    "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", str(o)],
                   check=True, capture_output=True, timeout=300)
    return sf.read(str(o), dtype="float32")[0]


def _ebur(x: np.ndarray, tmp: Path) -> tuple[float, float]:
    """ffmpeg ebur128 → (综合响度 LUFS, 真峰值 dBFS)。"""
    t = tmp / "e.wav"
    sf.write(str(t), x, SR, subtype="PCM_16")
    r = subprocess.run(
        ["ffmpeg", "-v", "info", "-nostats", "-i", str(t), "-af",
         "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True, timeout=300)
    s = r.stderr[r.stderr.rfind("Summary:"):]
    return (float(re.findall(r"I:\s*(-?[\d.]+) LUFS", s)[-1]),
            float(re.findall(r"Peak:\s*(-?[\d.]+) dBFS", s)[-1]))


def _c50(x: np.ndarray, frame_ms: float = 350.0) -> float:
    """标准 C50：350 ms 分帧逐帧算，取中位数。**只看发声帧**。

    两处都不省：

    - **不能整段算**。整段算会被长停顿污染 —— 干声能算出 -36 dB 这种无意义值。
    - **必须丢掉静音帧**。C50 测的是清晰度，而落在停顿上的帧量的是
      「混响往空隙里填了多少尾巴」——那是房间底噪，不是清晰度。测试信号
      静音占 42%，不丢帧的话中位数帧恰好落在停顿上，加混响后 ΔC50 会
      虚高到 **+2.98 dB**（同一份音频，真·清晰度变化其实只有 **-0.33 dB**），
      让人以为清晰度被改动了。丢帧后干湿帧数一致（16 / 16），ΔC50 正常。

    阈值取「低于整段均方功率的 2%」，即只要这一帧基本有内容就留下。
    """
    n = int(frame_ms / 1000 * SR)
    k = int(0.05 * SR)
    thresh = 0.02 * float((x.astype(np.float64) ** 2).mean())
    out = []
    for i in range(0, len(x) - n, n // 2):
        f = x[i:i + n]
        if float((f.astype(np.float64) ** 2).mean()) < thresh:
            continue
        out.append(10 * np.log10(((f[:k].astype(np.float64) ** 2).sum() + 1e-30)
                                 / ((f[k:].astype(np.float64) ** 2).sum() + 1e-30)))
    return float(np.median(out))


# ------------------------------------------------------------------ 验收项


def test_reverb_output_hits_the_podcast_loudness_spec(tmp_path):
    """加完混响成品仍然落在 -16 LUFS / -1.0 dBTP（见坑 3）。"""
    src = _to_mp3(_speechlike(), tmp_path / "dry.mp3")
    dry_lufs, _ = _ebur(_read48(src, tmp_path), tmp_path)
    # 前提：输入是**没归一**的，必须比 -16 低，否则「输出 -16」可能只是
    # 输入碰巧就在 -16，这条断言就白测了。
    assert dry_lufs < -16.5, f"测试信号本身就 {dry_lufs:.2f} LUFS，测不出归一"

    voxcpm_tts._apply_room_reverb(src)
    I, pk = _ebur(_read48(src, tmp_path), tmp_path)

    assert I == pytest.approx(-16.0, abs=0.7)
    assert pk <= -0.9, f"真峰值 {pk:.2f} dBFS 超了 -1.0 dBTP 规范"


def test_reverb_really_adds_a_room(tmp_path):
    """回归：加完混响确实更湿。

    这是踩过的最大一个 bug —— `convolve_verb` 原来按**峰值**归一化，混响场的
    峰均比是 15-18，越湿峰值越高，归一化就把整条信号按着峰值往下压。wet_db
    加 6.03 dB、归一化又减掉 8.64 dB，净效果 −2.61 dB，六版听起来一模一样。
    改成按 RMS 归一后这个断言才立得住。
    """
    src = _to_mp3(_speechlike(), tmp_path / "dry.mp3")
    dry = _read48(src, tmp_path)
    p_dry = float((dry.astype(np.float64) ** 2).mean())

    # 用同一条信号走一遍卷积（不经过 loudnorm），量扩散场相对干声的能量。
    ir = studio_ir(sr=SR, seed=0)
    wet_only = convolve_verb(dry, ir.copy() * np.r_[0.0, np.ones(len(ir) - 1)],
                             1.0, SR, normalize=False)
    rdr_db = 10 * np.log10(float((wet_only.astype(np.float64) ** 2).mean())
                           / p_dry)
    # Sabine 反推的物理基线：7×5×3 m / rt60 0.35 → 扩散场比干声低 5-9 dB
    assert -12.0 < rdr_db < -2.0, f"混响量 {rdr_db:.2f} dB 不在物理基线区间"

    # 成品的时长必须被 IR 尾巴拉长（没有尾巴就说明根本没卷上去）
    voxcpm_tts._apply_room_reverb(src)
    assert len(_read48(src, tmp_path)) > len(dry)


def test_reverb_does_not_smother_clarity(tmp_path):
    """ΔC50 < ±2 dB = 人耳听不出清晰度变化（见坑 4）。

    这是「听起来像演播室」而不是「听起来在大厅里喊话」的分界线。
    C50 掉到 -2 dB 以下字就开始发浑。
    """
    src = _to_mp3(_speechlike(), tmp_path / "dry.mp3")
    before = _c50(_read48(src, tmp_path))
    voxcpm_tts._apply_room_reverb(src)
    after = _c50(_read48(src, tmp_path))
    assert abs(after - before) < 2.0, f"ΔC50 {after - before:+.2f} dB，清晰度变了"


@pytest.mark.parametrize("params", [
    pytest.param({}, id="默认_物理基线"),
    pytest.param({"rt60": 0.20, "room": (4.0, 3.0, 2.4)}, id="小录音间"),
    pytest.param({"rt60": 0.60, "room": (10.0, 7.0, 3.5)}, id="大播音室"),
])
def test_reverb_params_change_the_room(params):
    """换房间参数 = 真的换了个房间（不是装饰性的参数）。

    RT60 的测量方法直接复用 test_room_ir 里那个已经踩过坑的 `measure_rt60`
    （软边带通 + 去掉直达声 + Schroeder 积分）。**不要在这里重写一份**：
    整段不分频地积分会被高频段主导（高频衰减快得多），三个房间全都被量成
    目标值的 0.56-0.64 倍，看起来像 bug，其实是量法错了。
    """
    from tests.test_room_ir import measure_rt60

    ir = studio_ir(sr=SR, seed=0, **params)
    target = params.get("rt60", 0.35)
    # 1 kHz 落在混响最长的中频段，用它代表这个房间的 RT60
    measured = measure_rt60(ir, 700.0, 1400.0, sr=SR)
    assert measured == pytest.approx(target, rel=0.15)


def test_reverb_failure_does_not_fail_the_whole_synthesis(tmp_path, monkeypatch):
    """混响是锦上添花 —— 它挂了必须退回干声交付，不能让整次合成判失败。"""
    seen = {}

    def boom(path, **kw):
        seen["called"] = True
        raise RuntimeError("模拟 ffmpeg 挂了")

    monkeypatch.setattr(voxcpm_tts, "_apply_room_reverb", boom)
    monkeypatch.setattr(voxcpm_tts, "synthesize",
                        lambda **kw: b"fake-mp3-bytes")
    monkeypatch.setattr(voxcpm_tts, "_resolve_wav",
                        lambda vid, *a, **k: ("/dev/null", ""))
    monkeypatch.setattr(voxcpm_tts, "_needs_resemble", lambda *a, **k: False)

    out = tmp_path / "o.mp3"
    logs = []
    path, _ = voxcpm_tts.synthesize_segments(
        [{"speaker": "甲", "text": "测试一段话。"}],
        voice_id="v", output_path=str(out), on_log=logs.append)

    assert seen["called"], "混响根本没被调用"
    assert Path(path).exists(), "混响失败后应该仍然交付干声"
    assert any("混响失败" in m for m in logs), f"没有把失败记进日志: {logs}"


def test_reverb_runs_once_on_the_concat_not_per_segment(tmp_path, monkeypatch):
    """混响必须整段做一次，不能逐段做（见坑 1）。

    逐段加混响时，每段的余响尾巴会盖住下一段的开头 —— 段 mp3 是首尾硬接的，
    中间没有静音，于是每个段边界都出现一次可听的「缝」。这条断言守的是
    「调用一次，作用在 concat 产物上」。
    """
    calls = []
    monkeypatch.setattr(
        voxcpm_tts, "_apply_room_reverb",
        lambda p, **kw: calls.append(Path(p)))
    monkeypatch.setattr(voxcpm_tts, "synthesize",
                        lambda **kw: b"fake-mp3-bytes")
    # 3 段会走 ffmpeg concat，fake-mp3-bytes 不是真 mp3 会失败 ——
    # 这条测试要验的是「调用一次、作用在 concat 产物上」，concat 本身不是重点。
    monkeypatch.setattr(voxcpm_tts, "_concat_mp3",
                        lambda out, files: Path(out).write_bytes(b"concat"))
    monkeypatch.setattr(voxcpm_tts, "_resolve_wav",
                        lambda vid, *a, **k: ("/dev/null", ""))
    monkeypatch.setattr(voxcpm_tts, "_needs_resemble", lambda *a, **k: False)

    out = tmp_path / "o.mp3"
    voxcpm_tts.synthesize_segments(
        [{"speaker": "甲", "text": "第一句。"},
         {"speaker": "甲", "text": "第二句。"},
         {"speaker": "甲", "text": "第三句。"}],
        voice_id="v", output_path=str(out))

    assert len(calls) == 1, f"混响被调用了 {len(calls)} 次（3 段 = 应该 1 次）"
    assert calls[0].name == out.name, (
        f"混响作用在 {calls[0].name} 上，不是 concat 产物 {out.name}")


def test_reverb_can_be_turned_off(tmp_path, monkeypatch):
    """reverb=False = 交付纯干声（对比用 / 给不喜欢混响的听众一条退路）。"""
    monkeypatch.setattr(voxcpm_tts, "synthesize",
                        lambda **kw: b"fake-mp3-bytes")
    monkeypatch.setattr(voxcpm_tts, "_resolve_wav",
                        lambda vid, *a, **k: ("/dev/null", ""))
    monkeypatch.setattr(voxcpm_tts, "_needs_resemble", lambda *a, **k: False)

    def boom(*a, **k):
        raise AssertionError("reverb=False 时不该调用混响")

    monkeypatch.setattr(voxcpm_tts, "_apply_room_reverb", boom)
    voxcpm_tts.synthesize_segments(
        [{"speaker": "甲", "text": "测试。"}],
        voice_id="v", output_path=str(tmp_path / "o.mp3"), reverb=False)


def test_reverb_params_are_forwarded(tmp_path, monkeypatch):
    """reverb_params 透传（要能换房间，不需要改默认）。"""
    seen = {}
    monkeypatch.setattr(
        voxcpm_tts, "_apply_room_reverb",
        lambda p, **kw: seen.update(kw))
    monkeypatch.setattr(voxcpm_tts, "synthesize",
                        lambda **kw: b"fake-mp3-bytes")
    monkeypatch.setattr(voxcpm_tts, "_resolve_wav",
                        lambda vid, *a, **k: ("/dev/null", ""))
    monkeypatch.setattr(voxcpm_tts, "_needs_resemble", lambda *a, **k: False)

    voxcpm_tts.synthesize_segments(
        [{"speaker": "甲", "text": "测试。"}],
        voice_id="v", output_path=str(tmp_path / "o.mp3"),
        reverb_params={"rt60": 0.22, "room": (5.0, 4.0, 2.6)})

    assert seen.get("rt60") == 0.22
    assert seen.get("room") == (5.0, 4.0, 2.6)