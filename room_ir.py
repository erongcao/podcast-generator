"""程序化生成房间脉冲响应（IR），用于给语音加演播室混响。

不用下载 IR 素材 —— 按声学模型合成：
  1. 直达声    t=0 的冲激
  2. 早期反射  镜像声源法：声源到各面墙的往返时间 = 2d/c，稀疏离散抽头
  3. 晚期混响  指数衰减噪声，τ = RT60/ln(1000) ≈ RT60/6.9
  4. 频变衰减  高频衰减快于低频（空气吸收 + 墙面吸声）——
               把噪声分 K 个频带各自按自己的 τ 衰减

卷积用 FFT（scipy.signal.fftconvolve），O(N log N)。

典型值：
  小演播室  rt60=0.25  pre_delay=6ms   room=5×4×2.8 m
  中演播室  rt60=0.40  pre_delay=9ms   room=7×5×3.0 m
  大播音室  rt60=0.60  pre_delay=14ms  room=10×7×3.5 m
"""
from __future__ import annotations

import numpy as np

_C = 343.0     # 声速 m/s
_REF_HZ = 1000.0   # rt60 的参考频率（房间声学的标准定义：1 kHz 处）


def _band_edges(n_bands: int, f_lo: float, f_hi: float) -> np.ndarray:
    """让 1 kHz 落在某条频带的**中心**的对数频带边界。

    `rt60` 的定义是「1 kHz 处的混响时间」，而衰减率按频带中心算
    `rt60 × hf_ratio**log2(fc/1000)`。所以 1 kHz 必须落在中心，落在**边界**
    上就全错 —— 曾经 1 kHz 落在边界，被算进中心 1687 Hz 的那条，实际衰减率
    变成 0.35 × 0.55^0.75 = 0.21 s，比声称的短 40%。

    带 i 的中心位于 `-a + (i+0.5)*step`，令某条中心为 0 ⇒ `a/b = (i+0.5)/(n-i-0.5)`。
    在允许范围内（下方不超 f_lo、上方不超 f_hi）选覆盖最宽的那个 i。
    最外两条再延伸到 f_lo / f_hi，保证全频段都有覆盖。
    """
    span_lo = np.log10(_REF_HZ / f_lo)
    span_hi = np.log10(f_hi / _REF_HZ)
    best = None
    for i in range(n_bands):
        num, den = i + 0.5, n_bands - i - 0.5
        b = min(span_hi, span_lo * den / num)
        a = b * num / den
        if best is None or a + b > best[0] + best[1]:
            best = (a, b)
    a, b = best
    edges = _REF_HZ * (10.0 ** np.linspace(-a, b, n_bands + 1))
    edges[0], edges[-1] = f_lo, f_hi
    return edges


def _band_split_masks(freqs: np.ndarray, n_bands: int,
                      edges: np.ndarray) -> list[np.ndarray]:
    """按给定边界切成 n_bands 个频带掩码（0/1）"""
    return [((freqs >= edges[i]) & (freqs < edges[i + 1])).astype(np.float64)
            for i in range(n_bands)]


def _band_reverberant_energy(room: tuple[float, float, float],
                             band_rt60: np.ndarray,
                             wet_db: float = 0.0) -> np.ndarray:
    """每条频带的「混响能量 / 直达声能量」（无 dB），从该频带的 RT60 反推。

    Sabine `RT60 = 0.161·V/(S·ᾱ)` 先给出每频带的吸声系数，再走扩散场
    `E_rev/E_dir = 8π·(1−ᾱ)/(S·ᾱ)`（`A_room = S·ᾱ/(1−ᾱ)` 的等价写法）。

    好处是**没有独立的混响量参数**：混响强弱完全由「房间多大 + 混响多长」
    决定，和 `rt60` 的物理定义自洽。`lf_ratio < 1` 会让低频 RT60 更短 →
    吸声更多 → 低频混响更少，这一点自动帮我们压住男声的低频轰鸣。

    **踩过的两个坑，都在这里：**
      1. 早期版本按**峰值**归一尾巴（`late_gain × 0.35`），但尾巴是 2 万多个
         样本的密集噪声，峰值 −11 dB 对应 **+22 dB 的总能量**。7×5×3 m 的
         演播室实测卷积增益 13×，干声被埋在混响下面 21 dB —— 听上去全是
         底噪，而且调哪个参数量出来都是「停顿变吵」，因为信号早被噪声主导了。
      2. 改成按**时域总能量**归一之后还是错 **+12.7 dB**。时域能量归一只
         对**白噪声**输入给出正确的 RDR；每个频带分到相等的**总**能量，但
         对数分频带里低频那条跨越的 bin 多得多，于是低频每 Hz 的能量碾压
         其他频带，而语音的能量恰恰压倒性地在低频 —— 卷积把混响能量全堆在
         干声本来就有能量的地方。必须让 IR 的**功率谱密度逐 bin 相同**，
         这样信号加权正好约掉，RDR 对任何输入都精确（见下面调用处的归一）。
    """
    L, W, H = room
    vol = L * W * H
    surf = 2.0 * (L * W + L * H + W * H)
    alpha = np.clip(0.161 * vol / (surf * np.asarray(band_rt60)), 0.03, 0.85)
    return (8.0 * np.pi * (1.0 - alpha) / (surf * alpha)) * 10.0 ** (wet_db / 10.0)


def _band_rt60(band_fc: np.ndarray, rt60: float, hf_ratio: float,
               lf_ratio: float) -> np.ndarray:
    """每条频带的 RT60：以 1 kHz 为基准，向上按 hf_ratio、向下按 lf_ratio 衰减。

    高频衰减快 = 空气吸收 + 墙面吸声（所有房间都成立）。
    低频衰减快 = **处理过的**房间：地毯、吸声墙、就座听众都在吃低频。
    未处理的音乐厅恰恰相反（低频衰减慢），那个用 lf_ratio > 1。
    """
    oct_up = np.maximum(np.log2(band_fc / _REF_HZ), 0.0)
    oct_dn = np.maximum(-np.log2(band_fc / _REF_HZ), 0.0)
    return np.clip(rt60 * hf_ratio ** oct_up * lf_ratio ** oct_dn,
                   rt60 * 0.2, rt60 * 2.0)


def studio_ir(
    rt60: float = 0.35,
    sr: int = 48000,
    pre_delay_ms: float = 8.0,
    room: tuple[float, float, float] = (7.0, 5.0, 3.0),
    source: tuple[float, float, float] = (0.35, 0.4, 0.15),
    n_bands: int = 6,
    hf_ratio: float = 0.55,
    lf_ratio: float = 0.70,
    early_gain: float = 0.25,
    wet_db: float = 0.0,
    wet_min_hz: float = 0.0,
    jitter_ms: float = 0.35,
    seed: int = 0,
) -> np.ndarray:
    """生成单声道房间脉冲响应。

    **混响量不是自由参数** —— 由 `room` 和 `rt60` 经 Sabine 反推出来
    （见 `_band_reverberant_energy`）。想「湿一点」调 `room` 或 `wet_db`，
    不要去调一个跟物理无关的增益。

    Args:
        rt60: **1 kHz 处**的混响时间（秒）。0.2-0.35 = 小演播室；0.4-0.5 = 中棚。
        pre_delay_ms: 直达声到第一次反射的时间（声源到最近墙距离）。
        room: 房间长宽高（米）。**决定混响量**：同样 rt60 下房间越大混响越弱
               （扩散场的混响能量密度随体积涨、随表面积涨）。
        source: 声源在房间内的归一化位置 (0-1, 0-1, 0-1)。
        n_bands: 频变衰减的频带数。
        hf_ratio: 每翻倍频率（向高频方向）RT60 的倍率。**<1 = 高频衰减更快**
               （空气吸收 + 墙面吸声），这是所有房间都成立的；>1 物理上不成立。
        lf_ratio: 每翻倍频率（向低频方向）RT60 的倍率。**<1 = 低频也衰减更快**
               —— 这是播音室（地毯 + 吸声处理 + 就座听众）的真实行为。

               低频 RT60 短 → 吸声多 → 低频混响更少（Sabine 自动推出，
               不需要额外参数），所以 `lf_ratio=0.70` 比 1.0 少很多低频轰鸣。
        early_gain: 早期反射占混响总**能量**的比例（0.15-0.4）。越大越"近"、
               越干。剩余能量归晚期尾巴。
        wet_db: 相对物理房间值的微调（±3 dB）。0 = 该尺寸房间的应有混响量。
        wet_min_hz: **只对混响尾巴**做高通（直达声和干声都不经过这个滤波器）。
               这是把「加混响」和「加重低频轰鸣」解耦的手段：VoxCPM 的低频
               轰鸣跟着发声走，任何在时间上摊开能量的操作都会把它堆高
               （实测发声段 +4…+7 dB）。切掉混响尾巴的低频后，增量降到 0。
               干声自己的低频**不会被削弱** —— 那是降噪该管的，不是混响该管的。
               0 = 不高通（保留完整房间感）；80 = 混响不带低频。
        jitter_ms: 早期反射的随机抖动（真实房间的墙面不完美）。
    """
    rng = np.random.default_rng(seed)

    # 先定衰减最快的频带有多长，据此分配长度，避免尾巴被截断
    edges = _band_edges(n_bands, 30.0, sr * 0.48)
    band_fc = np.sqrt(edges[:-1] * edges[1:])                     # 几何平均
    band_rt = _band_rt60(band_fc, rt60, hf_ratio, lf_ratio)
    length = int((band_rt.max() * 1.25 + pre_delay_ms / 1000.0 + 0.05) * sr)
    ir = np.zeros(length, dtype=np.float64)

    # ── 1. 直达声
    ir[0] = 1.0

    # ── 2. 早期反射（镜像声源法的一阶 + 二阶）
    L, W, H = room
    sx, sy, sz = (source[0] * L, source[1] * W, source[2] * H)
    cx, cy, cz = sx - L / 2, sy - W / 2, sz - H / 2

    refl = []
    for axis, c, half in (("x", cx, L / 2), ("y", cy, W / 2), ("z", cz, H / 2)):
        d = half - abs(c)                      # 到该面墙的距离
        t1 = 2.0 * d / _C                      # 一阶往返
        t2 = 2.0 * (2 * half - d) / _C if half > 0 else t1  # 二阶往返
        refl += [(t1, axis), (t2, axis)]

    for i, (t, axis) in enumerate(refl):
        tt = t + rng.normal(0.0, jitter_ms / 1000.0)
        idx = int((tt + pre_delay_ms / 1000.0) * sr)
        if 1 <= idx < length:
            # 反射面吸收：一阶明显，二阶更弱
            decay = 0.72 if i % 2 == 0 else 0.40
            ir[idx] += decay * (1.0 if rng.random() > 0.25 else -1.0)

    # ── 3+4. 晚期混响：分频带，各用自己的衰减率和混响能量
    n = length
    t = np.arange(n) / sr
    t_late = np.maximum(t - pre_delay_ms / 1000.0, 0.0)

    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    masks = _band_split_masks(freqs, n_bands, edges)

    # 各频带 RT60 在函数开头已按「1 kHz 为基准、每翻倍 ×hf_ratio」算好；
    # 再由它反推每条频带的混响/直达能量比（Sabine）
    rdr = _band_reverberant_energy(room, band_rt, wet_db)
    i_ref = int(np.argmin(np.abs(band_fc - _REF_HZ)))       # 含 1 kHz 的那条

    # 每个频带：带通噪声 × 指数包络，再归一到「该频带每 bin 的目标能量」
    noise = rng.normal(0.0, 1.0, n)
    Nf = np.fft.rfft(noise)
    late = np.zeros(n)
    for m, r6, e6 in zip(masks, band_rt, rdr):
        if m.sum() == 0:
            continue
        band = np.fft.irfft(Nf * m, n=n) * np.exp(-6.9078 * t_late / r6)
        # Parseval: 该带每 bin 的平均功率 = mean(x²)·n/m.sum()
        # → 要让**逐 bin** 功率 = e6（而不是每带总能量相等），必须这样定标。
        # 见 _band_reverberant_energy 里第二个坑的说明。
        rms_want = np.sqrt(e6 * m.sum() / n)
        band *= rms_want / np.sqrt((band ** 2).mean())
        late += band

    # 早期反射按含 1 kHz 那条的混响能量定标
    e_share = float(np.clip(early_gain, 0.0, 0.9))
    ir[1:] *= np.sqrt(e_share * rdr[i_ref] / max((ir[1:] ** 2).sum(), 1e-20))

    # 低频高通**只作用于混响尾巴**，直达声保持干净的冲激。
    # 踩过的坑：把高通加在含直达声的整个 IR 上，砖墙会把 delta 变成
    # 0.5 s 的 1/t sinc 振铃，卷到语音上等于给每个停顿都填进上一个音的尾巴。
    # 而直达声不是混响，本来就不该被滤波。
    if wet_min_hz > 0.0:
        k = min(n // 2, int(wet_min_hz * n / sr)) or 1
        late_spec = np.fft.rfft(late)
        late_spec[:k] = 0.0
        late = np.fft.irfft(late_spec, n=n)

    ir[1:] += late[1:]
    # 直达声在最后定死：早期把 late 归一按能量算，late[0] 会是个没有物理意义
    # 的小值（FFT 落点造成的），不写死的话直达声可能不再是全 IR 的最强成分
    # （实测 ir[0] 会掉到 0.83）。
    ir[0] = 1.0
    return ir.astype(np.float32)


def convolve_verb(dry: np.ndarray, ir: np.ndarray, wet: float = 1.0,
                  sr: int = 48000) -> np.ndarray:
    """把 IR 卷到干声上。wet=1.0 = 完全湿声（IR 含直达声），
    wet=0 = 全干。用 numpy FFT 免 scipy 依赖。

    输出归一化到**干声的峰值**。副作用要知道：增益取决于 IR 的总能量，
    所以换一组 IR 参数（比如 `wet_min_hz` 切掉低频）会让输出整体平移零点几 dB
    ——「湿声 A 比湿声 B 高 0.7 dB」量的其实是峰值归一化，不是房间差别。
    要跨变体比较电平，必须先各自归一到自己的 RMS（见 tests 里的做法）。
    """
    n = len(dry) + len(ir)
    size = 1 << (n - 1).bit_length()
    y = np.fft.irfft(np.fft.rfft(dry, size) * np.fft.rfft(ir, size), size)[:n]
    y = y / max(np.abs(y).max(), 1e-9) * max(np.abs(dry).max(), 1e-9)
    if wet < 1.0:
        # 干声要补零到 conv 的长度，否则 (1-wet)*dry 和 wet*y 形状对不上
        padded = np.zeros(n, dtype=y.dtype)
        padded[:len(dry)] = dry
        y = (1.0 - wet) * padded + wet * y
    return y.astype(np.float32)