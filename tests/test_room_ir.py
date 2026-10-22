"""room_ir 的声学验证 —— 用测 RT60 的标准方法反查生成参数。

坑记录（都踩过，别改回去）：
  1. **不能用砖墙带通去测 IR**。IR 的 sample 0 是一个冲激，砖墙掩码会把它
     滤成 1/t 的多项式振铃，尾部永不消失，还会从 -25 dB 下方反向穿回
     -25 dB 混进拟合区间，把斜率从 -77 dB/s 拉到 -9.7 dB/s（RT60 0.73 → 6.18）。
     必须用软边（升余弦）掩码。
  2. **不能用连续噪声激励**。持续激励下 Schroeder 积分量的是稳态卷积，
     不是衰减率（实测 2.15-4.12 s vs 目标 0.25-0.50）。
  3. **量 RT60 必须先去掉直达声**（ir[0]）。单位冲激的 DFT 幅度在每个 bin
     上都恰好是 1，给每条频带贡献的能量 = 该频带的 bin 数。混响定标到物理
     值之后直达声是压倒性的主导成分（3-9 kHz 段尾部只有直达声的 6-16%，
     15-45 Hz 段直达声占 104.7%）。不去掉的话，量到的是冲激不是混响。
     用 Schroeder 反向积分而不是短时窗拟合：用上全部数据、不受窗长影响，
     1 kHz 精度从 ±15% 提到 ±2%。
  4. **测 wet_min_hz 要测斜边两侧，不要测横跨斜边的均值**。斜边是
     cutoff/2→cutoff（40→80 Hz），30-82 Hz 这条带正好横跨它，60-82 Hz
     原样通过，整条带取均值只有 ~6 dB 降幅，看着像功能没生效。斜边以下
     （30-40 Hz）实际降 13-19 dB，斜边以上（82 Hz+）0.00 dB。
     另外混响场最低只到 30 Hz（`_band_edges(..., f_lo=30.0, ...)`），
     15-45 Hz 的能量全是 30-82 Hz 段的泄漏加上早期反射抽头。
  5. **砖墙 vs 升余弦，对混响场没有可闻差别**（rt60 三档实测峰均比和
     5-50 ms 能量只差 0.1 dB）。真正要守的是「**别滤 ir[0]**」——
     直达声被滤会挂 9 个测试。别把这两件事混为一谈。
  5. **跨变体比电平必须先各自归一**。`convolve_verb` 把输出归一化到干声峰值，
     增益取决于 IR 的总能量，所以换一组 IR 参数会整体平移零点几 dB
     （实测 wet_min_hz 切低频后整体 +0.73 dB）。绝对 dB 不可比。
  6. **测「低频有没有被加重」不能用短时 FFT**。4096 点窗在近静音的停顿帧上，
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
                     _band_rt60, convolve_verb, studio_ir)

SR = 48000
TD_TMP = Path(tempfile.mkdtemp(prefix="room_ir_test_"))


def _soft_bandpass(freqs: np.ndarray, lo: float, hi: float,
                   ramp_hz: float) -> np.ndarray:
    """带升余弦斜边的带通掩码 —— 斜边是消除冲激振铃的关键。"""
    up = np.clip((freqs - lo) / ramp_hz, 0.0, 1.0)
    down = np.clip((hi - freqs) / ramp_hz, 0.0, 1.0)
    return ((0.5 - 0.5 * np.cos(np.pi * up))
            * (0.5 - 0.5 * np.cos(np.pi * down)))


def measure_rt60(ir: np.ndarray, lo: float, hi: float, expect: float = 0.0,
                 sr: int = SR) -> float | None:
    """测某个频段的 RT60（秒）。返回 None 表示衰减不够深，测不出来。

    **必须先去掉直达声**（ir[0]）。冲激的 DFT 幅度在每个 bin 上都恰好是 1，
    它给每条频带贡献的能量 = 该频带的 bin 数。混响定标到物理值之后，
    直达声在 IR 里是压倒性的主导成分（3-9 kHz 段实测尾部只有直达声的
    6-16%，15-45 Hz 段直达声占 104.7%），不去掉的话量的是冲激不是混响。

    用 Schroeder 反向积分而不是短时窗拟合，两个原因：
      1. 用上全部数据，不受窗长影响。早先的短时窗版本要靠 `expect` 猜窗长，
         3-9 kHz 猜低了一倍 → 5.7 ms 窗根本压不住密集噪声的抖动，
         单调性过滤器卡死 → 返回 None；就算没卡死也量出 0.189 s（真值 0.070）。
      2. 去掉直达声之后，1 kHz 处的精度从 ±15% 提到 ±2%
         （rt60=0.25 实测 0.288 → 0.244；0.35 → 0.349；0.50 → 0.471）。
    """
    wet = ir.copy()
    wet[0] = 0.0
    n = len(wet)
    freqs = np.fft.rfftfreq(n, 1 / sr)
    mask = _soft_bandpass(freqs, lo, hi, max(300.0, (hi - lo) / 4))
    y = np.fft.irfft(np.fft.rfft(wet) * mask, n=n)

    edc = np.cumsum((y.astype(np.float64) ** 2)[::-1])[::-1]
    d = 10 * np.log10(edc / edc.max() + 1e-30)

    def cross(db_level: float) -> float | None:
        i = np.nonzero(d <= db_level)[0]
        return float(i[0] / sr) if len(i) else None

    t5, t25 = cross(-5.0), cross(-25.0)
    if t5 is None or t25 is None:
        return None
    return (t25 - t5) * 3.0        # T20 → RT60


def _reverberant_over_direct_db(sig: np.ndarray, ir: np.ndarray) -> float:
    """输出里「纯混响场」相对输入的能量比（dB）。

    把 IR 的直达声去掉再卷，得到的就只剩扩散场。

    `normalize=False` 是关键：默认的峰值归一化会按 IR 的总能量把增益整体拉平，
    而混响/干声的能量比恰恰**就是** IR 总能量决定的 —— 归一化之后量到的只剩
    波峰因子。踩过的坑：wet_db 加 3 dB 时这个量纹丝不动。
    """
    ir_wet = ir.copy()
    ir_wet[0] = 0.0
    y = convolve_verb(sig, ir_wet, wet=1.0, sr=SR, normalize=False)
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
    # 窗长必须按该频段的**真实** RT60 定，不能拍脑袋乘个系数。3-9 kHz 横跨
    # 中心 2723 Hz 和 10176 Hz 两条带，从频带结构直接推出来。
    edges = _band_edges(6, 30.0, SR * 0.48)
    centers = np.sqrt(edges[:-1] * edges[1:])
    band_rt = _band_rt60(centers, target, 0.55, 0.70)
    covered = [(lo, hi, band_rt[i]) for i, (lo, hi) in
               enumerate(zip(edges[:-1], edges[1:])) if hi > 3000 and lo < 9000]
    expect_high = float(np.mean([r for _, _, r in covered]))

    mid = measure_rt60(ir, 600.0, 1400.0, target)
    high = measure_rt60(ir, 3000.0, 9000.0, expect_high)
    assert mid is not None and high is not None, (
        f"{target}: 测不出 RT60（mid={mid}, high={high}, "
        f"expect_high={expect_high:.3f}）")
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

    必须在**去掉直达声之后**量，否则量不到混响：单位冲激的 DFT 幅度在每个
    bin 上都恰好是 1，所以它给每个频带贡献的能量 = 该频带的 bin 数。
    混响量定标到物理值之后（1 kHz 带的 RDR ≈ −4.6 dB），15-45 Hz 这条带的
    能量**完全被直达声盖住** —— 实测 delta 占了 104.7%，尾巴在低频根本
    不可见。不去掉 ir[0] 的话，这个测试量的是冲激，跟 wet_min_hz 没关系。

    不能拿 `wet_db=-60` 的版本当「只有直达声」的参照：wet_db 会把早期反射
    一起缩掉，而早期反射是 6 根稀疏抽头、频谱是梳状的，500-1200 Hz 落在
    梳齿上、15-45 Hz 落在齿间。端到端的实际效果由下面那个测试断言。
    """
    def ir_bin_db(ir, lo, hi):
        """逐 bin 平均幅度（dB）—— 尺度无关，不受整体归一化影响。"""
        f = np.fft.rfftfreq(len(ir), 1 / SR)
        X = np.abs(np.fft.rfft(ir))
        m = (f >= lo) & (f < hi)
        return 20 * np.log10(X[m].mean() + 1e-20)

    def wet_only(ir):
        w = ir.copy()
        w[0] = 0.0          # 去掉直达声，剩下的才是混响场
        return w

    plain = wet_only(studio_ir(rt60=0.35, sr=SR, seed=0))
    hp = wet_only(studio_ir(rt60=0.35, sr=SR, seed=0, wet_min_hz=80.0))

    # 混响尾巴的低频必须真的被切掉
    assert ir_bin_db(hp, 30, 40) < ir_bin_db(plain, 30, 40) - 30.0, (
        f"斜边以下的 30-40 Hz 只降了 "
        f"{ir_bin_db(hp, 30, 40) - ir_bin_db(plain, 30, 40):+.1f} dB")

    # 中高频完全不受影响
    assert ir_bin_db(hp, 500, 1200) == pytest.approx(ir_bin_db(plain, 500, 1200),
                                                     abs=0.01)
    # 直达声必须原封不动 —— 被切就会产生 sinc 振铃（见下面的回归测试）
    assert studio_ir(rt60=0.35, sr=SR, seed=0, wet_min_hz=80.0)[0] == pytest.approx(
        1.0, abs=1e-6), "直达声被改动"


def test_wet_min_hz_cuts_the_output_low_band_without_touching_the_mid():
    """端到端行为：wet_min_hz 必须实打实切掉**混响自己带的**低频，且不动中频。

    量的是「纯混响场」（去掉 IR 的直达声再卷），不是完整输出。原因是混响量
    定标到物理值之后低频本来就很少：完整输出里直达声压倒性地占着 15-45 Hz，
    wet_min_hz 在那里只能切掉 0.8 dB —— 那不是说功能没用，是说**要切的那份
    能量本来就不在完整输出里**。拿完整输出断言会逼出一个假的失败。

    用整段 FFT 量，不用短时窗（见模块 docstring 的坑 5）。
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

    def reverb_only(ir):
        w = ir.copy()
        w[0] = 0.0
        return convolve_verb(dry, w, 1.0, SR, normalize=False)

    full = reverb_only(studio_ir(rt60=0.35, sr=SR, seed=1))
    cut = reverb_only(studio_ir(rt60=0.35, sr=SR, seed=1, wet_min_hz=80.0))
    lf_cut = band_energy_db(full, 30, 40) - band_energy_db(cut, 30, 40)
    mid_shift = band_energy_db(cut, 300, 3000) - band_energy_db(full, 300, 3000)
    # 降幅到 13-19 dB 就封顶了，是**预期的**，不是没滤干净：
    #   1. 斜边是 cutoff/2→cutoff 一倍频程，30-40 Hz 本来就在截止点以下，
    #      IR 侧已经压到 -80 dB/bin（只剩 FFT 舍入底噪）；
    #   2. 但 IR 是有限长的，滤波器在 30-40 Hz 的残余主要来自 40-45 Hz
    #      那些「斜边上」的 bin 经 DTFT 主瓣渗过来；
    #   3. 干声在这个合成信号里 30-40 Hz 高达 +30…+37 dB（28 Hz 隆隆声），
    #      乘完还是很大。
    # 实测 4 个 seed：13.5 / 19.1 / 15.2 / 14.4 dB。
    assert lf_cut > 10.0, (
        f"混响的 30-40 Hz 只降了 {lf_cut:.1f} dB，wet_min_hz 没起作用")
    assert abs(mid_shift) < 0.1, (
        f"中频 300-3000 Hz 反而动了 {mid_shift:+.3f} dB —— 动的不该是它"
        "（实测 4 个 seed 全在 ±0.011 dB 内）")


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