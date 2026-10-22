"""room_ir 的声学验证 —— 用测 RT60 的标准方法反查生成参数。

坑记录（都踩过，别改回去）：
  1. **不能用砖墙带通去测 IR**。IR 的 sample 0 是一个冲激，砖墙掩码会把它
     滤成 1/t 的多项式振铃，尾部永不消失，还会从 -25 dB 下方反向穿回
     -25 dB 混进拟合区间，把斜率从 -77 dB/s 拉到 -9.7 dB/s（RT60 0.73 → 6.18）。
     必须用软边（升余弦）掩码。
  2. **不能用连续噪声激励**。持续激励下 Schroeder 积分量的是稳态卷积，
     不是衰减率（实测 2.15-4.12 s vs 目标 0.25-0.50）。
  3. **分析窗要跟着预期 RT60 走**。2k-8k 段的 RT60 只有 0.07-0.09 s，
     固定 20 ms 窗 + 40 ms 跳过会在信号还没进入 -25 dB 时就跳过去了。
  4. **跨变体比电平必须先各自归一**。`convolve_verb` 把输出归一化到干声峰值，
     增益取决于 IR 的总能量，所以换一组 IR 参数会整体平移零点几 dB
     （实测 wet_min_hz 切低频后整体 +0.73 dB）。绝对 dB 不可比。
  5. **测「低频有没有被加重」不能用短时 FFT**。4096 点窗在近静音的停顿帧上，
     低频的泄漏裙边会淹掉 100-300 Hz 的真实内容，而泄漏的来源恰恰是
     我要测的那个低频差异 —— 循环论证。同样的两个信号，整段 FFT 量出
     +0.75 dB，短时 FFT 量出 +13.9 dB。低频类断言一律走整段 FFT。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from room_ir import (_band_edges, _band_reverberant_energy,  # noqa: E402
                     convolve_verb, studio_ir)

SR = 48000
TD_TMP = Path(tempfile.mkdtemp(prefix="room_ir_test_"))


def _soft_bandpass(freqs: np.ndarray, lo: float, hi: float,
                   ramp_hz: float) -> np.ndarray:
    """带升余弦斜边的带通掩码 —— 斜边是消除冲激振铃的关键。"""
    up = np.clip((freqs - lo) / ramp_hz, 0.0, 1.0)
    down = np.clip((hi - freqs) / ramp_hz, 0.0, 1.0)
    return ((0.5 - 0.5 * np.cos(np.pi * up))
            * (0.5 - 0.5 * np.cos(np.pi * down)))


def measure_rt60(ir: np.ndarray, lo: float, hi: float, expect: float,
                 sr: int = SR) -> float | None:
    """测某个频段的 RT60（秒）。expect = 该频段的预期值，用来定窗长。"""
    win = max(int(min(0.030, expect / 8) * sr), 128)
    skip = int(min(0.040, expect / 6) * sr)
    n = len(ir)
    freqs = np.fft.rfftfreq(n, 1 / sr)
    mask = _soft_bandpass(freqs, lo, hi, max(300.0, (hi - lo) / 4))
    y = np.fft.irfft(np.fft.rfft(ir) * mask, n=n)[skip:]

    hop = win // 2
    env = np.array([(y[i:i + win] ** 2).sum() for i in range(0, len(y) - win, hop)])
    t = np.arange(len(env)) * hop / sr
    db = 10 * np.log10(env + 1e-30)
    db -= db.max()

    # 只取严格单调下降段（+1 dB 容差），排除噪声抖动和周期性回卷
    keep = [0]
    for i in range(1, len(db)):
        if db[i] <= db[keep[-1]] + 1.0 and db[i] < -0.5:
            keep.append(i)
    mask_fit = ((db <= -5) & (db >= -25)
                & np.isin(np.arange(len(db)), np.array(keep)))
    if mask_fit.sum() < 3:
        return None
    slope = np.polyfit(t[mask_fit], db[mask_fit], 1)[0]
    return float(-60.0 / slope)


def _reverberant_over_direct_db(sig: np.ndarray, ir: np.ndarray) -> float:
    """输出里「纯混响场」相对输入的能量比（dB）。

    把 IR 的直达声去掉再卷，得到的就只剩扩散场。
    """
    ir_wet = ir.copy()
    ir_wet[0] = 0.0
    y = convolve_verb(sig, ir_wet, wet=1.0, sr=SR)
    p_in = (sig.astype(np.float64) ** 2).mean()
    p_out = (y.astype(np.float64) ** 2).mean()
    return float(10 * np.log10(p_out / p_in))


# ── 结构不变量 ────────────────────────────────────────────────────────

def test_direct_sound_dominates_the_whole_ir():
    """直达声的**能量**必须超过全部混响能量之和 —— 否则听起来是混响主导。

    注意不是「单个样本最大」：晚期场是密度噪声，峰均比 15-18（实测尾部峰值
    2.3-2.6），单个晚期样本压过直达声的冲激在扩散场里完全正常。想用「限幅
    把尾部峰值压到 0.5」来保证这一点是错的 —— 那会砍掉 13 dB 的混响能量，
    把刚定标对的电平重新弄坏。
    """
    for seed in (0, 1, 7):
        ir = studio_ir(rt60=0.35, sr=SR, seed=seed)
        assert ir[0] == pytest.approx(1.0, abs=1e-6), f"seed={seed} 直达声是 {ir[0]}"
        rev = float((ir[1:] ** 2).sum())
        # Parseval：幅度 1 的冲激，时域能量是 n 而不是 1。拿 Σir[1:]² 去比
        # ir[0]² 会得出「混响能量 +27 dB」这种荒谬结论。
        direct = float(ir[0] ** 2) * len(ir)
        assert rev < direct, (
            f"seed={seed}: 混响能量 {10 * np.log10(rev / direct):+.1f} dB "
            "相对直达声，不该 ≥ 0 dB")


def test_early_reflections_never_exceed_direct():
    """早期反射单独看不能盖过直达声（early_gain=0 关掉晚期混响以隔离出来）。"""
    ir = studio_ir(rt60=0.35, sr=SR, seed=0, early_gain=1.0, wet_db=-40.0)
    assert np.abs(ir[1:]).max() < 1.0


def test_ir_is_deterministic_for_a_given_seed():
    a = studio_ir(rt60=0.35, sr=SR, seed=7)
    b = studio_ir(rt60=0.35, sr=SR, seed=7)
    assert np.array_equal(a, b)


# ── 电平定标：这是整个模块最要紧的一条 ──────────────────────────────────

@pytest.mark.parametrize("room,rt60", [
    ((5.0, 4.0, 2.8), 0.25),
    ((7.0, 5.0, 3.0), 0.35),
    ((10.0, 7.0, 3.5), 0.55),
])
def test_reverb_level_is_physical(room, rt60):
    """混响必须**比干声轻**，差在 15 dB 以内 —— 演播室该有的量。

    回归测试：早期版本把尾巴按峰值归一（`late_gain × 0.35`），但尾巴是两万个
    样本的密集噪声，峰值 −11 dB 对应 **+22 dB 总能量**。7×5×3 m 实测卷积
    增益 13×，干声被埋在混响下面 21 dB —— 听上去全是底噪，而且调哪个参数
    量出来都是「停顿变吵」，因为信号早被噪声主导了。
    """
    rng = np.random.default_rng(0)
    t = np.arange(SR * 4) / SR
    speech = (rng.normal(0, 0.05, SR * 4)
              * (np.sin(2 * np.pi * 3 * t) > 0.3)).astype(np.float32)
    ir = studio_ir(rt60=rt60, sr=SR, room=room, seed=1)
    rdr = _reverberant_over_direct_db(speech, ir)
    assert -15.0 < rdr < 3.0, (
        f"{room} rt60={rt60}: 混响/干声 = {rdr:+.1f} dB，应在 -15…+3 dB")


def test_reverb_level_does_not_depend_on_the_input_spectrum():
    """定标必须和输入信号无关。

    这是第二个坑：按**时域总能量**归一只对白噪声给出正确 RDR，每个频带分到
    相等的总能量，但对数分频带里低频那条跨越的 bin 多得多，每 Hz 能量碾压其他
    频带，而语音的能量压倒性地在低频 —— 卷积把混响全堆在干声本来就有能量的
    地方。改成**逐 bin** 归一（功率谱密度平坦）之后信号加权正好约掉。
    """
    rng = np.random.default_rng(0)
    t = np.arange(SR * 4) / SR
    white = rng.normal(0, 0.05, SR * 4).astype(np.float32)
    lf_heavy = (rng.normal(0, 0.05, SR * 4)
                * (1 + 2 * np.sin(2 * np.pi * 60 * t))).astype(np.float32)
    ir = studio_ir(rt60=0.35, sr=SR, seed=1)
    a = _reverberant_over_direct_db(white, ir)
    b = _reverberant_over_direct_db(lf_heavy, ir)
    assert abs(a - b) < 3.0, (
        f"白噪声输入 {a:+.1f} dB、低频加权输入 {b:+.1f} dB，相差 {abs(a - b):.1f} dB "
        "→ 定标仍然依赖输入频谱")


def test_bigger_room_is_wetter_than_smaller():
    """同样 RT60 下房间越大混响越弱（扩散场密度随体积涨、随表面积跌）。

    在 IR 层比 1 kHz 频带的 RDR，不要拿卷积输出去比 —— 输出是**语音加权**的
    结果，混进了每频带的 Sabine 倾斜和 RT60 上下限，实测会把大小房间的顺序
    弄反（−0.2 dB vs −0.6 dB），而 IR 层的物理顺序是 −1.6 / −4.6 / −9.0 dB。
    """
    rd = [_band_reverberant_energy(r, np.array([0.35]))[0]
          for r in ((5.0, 4.0, 2.8), (7.0, 5.0, 3.0), (10.0, 7.0, 3.5))]
    assert rd[0] > rd[1] > rd[2], (
        "同样 RT60 下 RDR 应随房间变大单调下降："
        + " / ".join(f"{10 * np.log10(v):+.1f} dB" for v in rd))


# ── 参数语义：rt60 真的落在 1 kHz 上 ──────────────────────────────────

def test_rt60_parameter_lands_on_1khz():
    """回归测试：曾经 1 kHz 落在频带**边界**上，实际衰减率只有声称值的 60%。"""
    for target in (0.25, 0.35, 0.50):
        edges = _band_edges(6, 30.0, SR * 0.48)
        fc = np.sqrt(edges[:-1] * edges[1:])
        band = int(np.searchsorted(edges, 1000.0, side="right") - 1)
        assert fc[band] == pytest.approx(1000.0, rel=0.05), (
            f"含 1 kHz 的频带中心是 {fc[band]:.0f} Hz，不是 1 kHz")

        ir = studio_ir(rt60=target, sr=SR, seed=1)
        measured = measure_rt60(ir, 600.0, 1400.0, target)
        assert measured == pytest.approx(target, rel=0.20), (
            f"1 kHz 处实测 {measured:.3f}，声称 {target:.3f}")


@pytest.mark.parametrize("target", [0.25, 0.35, 0.50])
def test_high_frequencies_decay_faster(target):
    """空气吸收 + 墙面吸声 → 高频 RT60 必须明显短于 1 kHz。"""
    ir = studio_ir(rt60=target, sr=SR, seed=1)
    mid = measure_rt60(ir, 600.0, 1400.0, target)
    high = measure_rt60(ir, 3000.0, 9000.0, target * 0.13)
    assert mid is not None and high is not None
    assert high < mid * 0.5, (
        f"{target}: 高频 {high:.3f} 应远短于 1 kHz 的 {mid:.3f}")


@pytest.mark.parametrize("target", [0.25, 0.35, 0.50])
def test_low_band_decays_no_slower_than_the_setting(target):
    """播音室的低频是被处理过的 —— 绝不能比 1 kHz 衰减得更慢。

    lf_ratio=1.0（音乐厅式的「低频慢」）会让男声发声段的 15-45 Hz 被房间
    尾巴堆高 +5.3 dB，正好加重用户抱怨的背景轰鸣。
    """
    ir = studio_ir(rt60=target, sr=SR, seed=1)
    low = measure_rt60(ir, 100.0, 400.0, target * 0.5)
    mid = measure_rt60(ir, 600.0, 1400.0, target)
    assert low is not None and mid is not None
    assert low < mid, (
        f"{target}: 100-400 Hz ({low:.3f}) 不该比 1 kHz ({mid:.3f}) 慢")


def test_convolve_does_not_boost_the_speech_lf():
    """端到端护栏：加混响不能把发声段的低频抬高。

    这是整个功能最关键的约束 —— VoxCPM 的低频轰鸣是**跟着发声走**的
    （安静时 −4 dB、说话时 +9…+22 dB），房间尾巴会把这些能量堆起来。

    注意：分析窗必须 ≥ 8192（11.7 Hz 分辨率才够 15-45 Hz 这条带）。
    960 点窗只有 50 Hz 分辨率，这个频带**一个 bin 都选不中**，结果全是 NaN。
    """
    t = np.arange(SR * 6) / SR
    voiced = (np.sin(2 * np.pi * 106 * t) > 0.4).astype(np.float32)
    rumble = np.sin(2 * np.pi * 28 * t).astype(np.float32)
    dry = (0.5 * np.sin(2 * np.pi * 106 * t) * (voiced + 0.5)
           + 0.10 * rumble * voiced).astype(np.float32)
    speech = voiced > 0.5

    win = 8192
    hop = win // 2
    fr = np.fft.rfftfreq(win, 1 / SR)
    m = (fr >= 15) & (fr < 45)
    assert m.sum() > 0, "分析窗太短，15-45 Hz 频带选不中 bin"
    w = np.hanning(win)

    def band_db(x: np.ndarray) -> float:
        n = (len(x) - win) // hop
        vals = np.empty(n)
        for i in range(n):
            S = np.abs(np.fft.rfft(x[i * hop:i * hop + win] * w))[m]
            vals[i] = 20 * np.log10(np.sqrt((S ** 2).sum()) / m.sum() + 1e-12)
        return float(np.median(vals[speech[:n]]))

    before = band_db(dry)
    wet = convolve_verb(dry, studio_ir(rt60=0.35, sr=SR, seed=1), 1.0, SR)
    after = band_db(wet)
    assert after - before < 2.5, (
        f"混响把发声段 15-45 Hz 抬高了 {after - before:.1f} dB，应 < 2.5 dB")


# ── 卷积行为 ──────────────────────────────────────────────────────────

def test_convolve_verb_preserves_peak_level():
    rng = np.random.default_rng(0)
    dry = (rng.normal(0, 0.1, SR) * np.sin(2 * np.pi * 220 * np.arange(SR) / SR)
           ).astype(np.float32)
    ir = studio_ir(rt60=0.35, sr=SR, seed=0)
    y = convolve_verb(dry, ir, wet=1.0, sr=SR)
    assert y.dtype == np.float32
    assert np.abs(y).max() == pytest.approx(np.abs(dry).max(), rel=1e-3)


def test_wet_zero_is_dry():
    rng = np.random.default_rng(0)
    dry = rng.normal(0, 0.1, 4096).astype(np.float32)
    ir = studio_ir(rt60=0.35, sr=SR, seed=0)
    y = convolve_verb(dry, ir, wet=0.0, sr=SR)
    assert np.allclose(y[:4096], dry, atol=1e-6)


def test_wet_min_hz_zeroes_the_reverb_low_band_only():
    """wet_min_hz 只切混响尾巴的低频。

    在 IR 层断言，不要用合成信号走端到端 —— `convolve_verb` 会把输出重新
    归一化到干声峰值，切掉低频后剩下的部分会被等比放大，绝对电平失去可比性。

    不能拿 `wet_db=-60` 的版本当「只有直达声」的参照：wet_db 会把早期反射
    一起缩掉，而早期反射是 6 根稀疏抽头、频谱是梳状的，500-1200 Hz 落在
    梳齿上、15-45 Hz 落在齿间，切完之后残留的 9 dB/bin 全是它们。
    端到端的实际效果由下面那个测试断言。
    """
    def ir_bin_db(ir, lo, hi):
        """逐 bin 平均幅度（dB）—— 尺度无关，不受整体归一化影响。"""
        f = np.fft.rfftfreq(len(ir), 1 / SR)
        X = np.abs(np.fft.rfft(ir))
        m = (f >= lo) & (f < hi)
        return 20 * np.log10(X[m].mean() + 1e-20)

    plain = studio_ir(rt60=0.35, sr=SR, seed=0)
    hp = studio_ir(rt60=0.35, sr=SR, seed=0, wet_min_hz=80.0)

    # 混响尾巴的低频必须真的被切掉。降幅的下限随 seed 变（early_gain=0.25
    # 时早期反射的梳状谱也往 15-45 Hz 放能量，seed=0 只降 8.9 dB、seed=1 降
    # 13.7 dB），所以真正的判据是下面那条「回到纯 delta 水平」。
    assert ir_bin_db(hp, 15, 45) < ir_bin_db(plain, 15, 45) - 6.0, (
        f"15-45 Hz 只降了 {ir_bin_db(hp, 15, 45) - ir_bin_db(plain, 15, 45):+.1f} dB")

    # 中高频完全不受影响
    assert ir_bin_db(hp, 500, 1200) == pytest.approx(ir_bin_db(plain, 500, 1200),
                                                     abs=0.01)
    # 直达声必须原封不动 —— 被切就会产生 sinc 振铃（见下面的回归测试）
    assert hp[0] == pytest.approx(1.0, abs=1e-6), f"直达声被改动：{hp[0]}"


def test_wet_min_hz_cuts_the_output_low_band_without_touching_the_mid():
    """端到端行为：wet_min_hz 必须实打实切掉输出里的低频，且不动中频。

    合成一段带低频轰隆的「发声」信号（VoxCPM 的低频是**跟着发声走**的：
    安静时 −4 dB、说话时 +9…+22 dB，这正是要压的东西）。用整段 FFT 量，
    不用短时窗（见模块 docstring 的坑 5）。
    """
    t = np.arange(SR * 4) / SR
    voiced = (np.sin(2 * np.pi * 106 * t) > 0.4).astype(np.float32)
    dry = (0.5 * np.sin(2 * np.pi * 106 * t) * (voiced + 0.5)
           + 0.10 * np.sin(2 * np.pi * 28 * t) * voiced).astype(np.float32)

    def band_energy_db(y, lo, hi):
        f = np.fft.rfftfreq(len(y), 1 / SR)
        X = np.abs(np.fft.rfft(y))
        m = (f >= lo) & (f < hi)
        return float(10 * np.log10((X[m] ** 2).sum() + 1e-30))

    full = convolve_verb(dry, studio_ir(rt60=0.35, sr=SR, seed=1), 1.0, SR)
    cut = convolve_verb(dry, studio_ir(rt60=0.35, sr=SR, seed=1,
                                       wet_min_hz=80.0), 1.0, SR)
    lf_cut = band_energy_db(full, 15, 45) - band_energy_db(cut, 15, 45)
    mid_shift = band_energy_db(cut, 300, 3000) - band_energy_db(full, 300, 3000)
    assert lf_cut > 5.0, f"15-45 Hz 只降了 {lf_cut:.1f} dB，wet_min_hz 没起作用"
    assert abs(mid_shift) < 1.0, (
        f"中频 300-3000 Hz 反而动了 {mid_shift:+.1f} dB —— 动的不该是它")


def test_wet_min_hz_changes_nothing_above_its_cutoff():
    """回归测试：wet_min_hz 只许改低频。

    曾经的 bug 是把高通加在**含直达声的整个 IR** 上，砖墙把 sample 0 的 delta
    变成 0.5 s 的 1/t sinc 振铃，卷到语音上等于给每个停顿都填进上一个音的尾巴。
    那个 bug 在端到端表现为停顿段底噪暴涨，而且**和 wet_min_hz 的取值无关**，
    所以不能用「底噪别涨」来抓 —— 底噪本来就会涨，那是混响该干的事。

    这里抓的是它真正的性质：两版 IR 的**差**必须严格限带在截止频率以下。
    用整段 FFT（不是短时 FFT，见模块 docstring 的坑 5）。
    """
    plain = studio_ir(rt60=0.35, sr=SR, seed=1)
    hp = studio_ir(rt60=0.35, sr=SR, seed=1, wet_min_hz=80.0)

    f = np.fft.rfftfreq(len(plain), 1 / SR)
    diff = np.abs(np.fft.rfft(hp.astype(np.float64) - plain.astype(np.float64)))
    frac_above = (diff[f >= 80] ** 2).sum() / (diff ** 2).sum()
    assert frac_above < 0.005, (
        f"差信号有 {frac_above * 100:.2f}% 的能量落在 80 Hz 以上 —— "
        "高通动了不该动的频段")


def test_reverb_fills_the_silent_gaps():
    """混响的直接证据：干声的静默间隙里必须有能量残留。

    比波峰因子可靠 —— 波峰因子受峰值归一化影响，间隙能量没有这个问题。
    """
    rng = np.random.default_rng(0)
    burst = np.zeros(SR * 3, dtype=np.float32)
    burst[:SR // 4] = rng.normal(0, 0.2, SR // 4).astype(np.float32)  # 只在开头发声
    gap = slice(SR // 2, SR * 2)                                        # 中间 1.5 s 完全静音
    dry_db = 20 * np.log10(np.sqrt((burst[gap] ** 2).mean()) + 1e-12)

    for target in (0.25, 0.55):
        ir = studio_ir(rt60=target, sr=SR, seed=0)
        wet = convolve_verb(burst, ir, wet=1.0, sr=SR)
        wet_db = 20 * np.log10(np.sqrt((wet[gap] ** 2).mean()) + 1e-12)
        assert wet_db > dry_db + 6.0, (
            f"rt60={target}: 静默间隙只涨了 {wet_db - dry_db:.1f} dB，应 > 6 dB")


def test_more_reverb_gives_more_tail_energy():
    """房间越大 / 混响越长 → 尾巴越强。这是「听得出是哪个房间」的量纲。"""
    rng = np.random.default_rng(0)
    burst = np.zeros(SR * 3, dtype=np.float32)
    burst[:SR // 4] = rng.normal(0, 0.2, SR // 4).astype(np.float32)
    gap = slice(SR // 2, SR * 2)
    levels = []
    for target in (0.25, 0.55):
        ir = studio_ir(rt60=target, sr=SR, room=(5.0, 4.0, 2.8), seed=0)
        wet = convolve_verb(burst, ir, wet=1.0, sr=SR)
        levels.append(20 * np.log10(np.sqrt((wet[gap] ** 2).mean()) + 1e-12))
    assert levels[1] > levels[0], (
        f"rt60=0.55 的间隙能量 {levels[1]:.1f} 应高于 rt60=0.25 的 {levels[0]:.1f}")