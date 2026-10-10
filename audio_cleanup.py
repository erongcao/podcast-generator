#!/usr/bin/env python3
"""
参考音频标准化清洗（为 VoxCPM 语音克隆做前置处理）

问题：用户在不同地点、用不同 mic 给同事录的参考音频，底噪、混响、
mic 距离、电平均不一样。VoxCPM 克隆时把这些「环境特征」学进 timbre，
导致合成输出里主播甲像咖啡馆、主播乙像会议室，组合起来听感割裂。

解决：入库时统一过一道清洗链，让所有参考音频「同一录音棚」。

Filter chain（顺序很重要）：
  1. ffmpeg: highpass=f=80 + lowpass=f=12000 → 频谱边缘清理
  2. resemble-enhance-mlx (ML) → 降噪 + 去混响
  3. loudnorm two-pass → 命中 -16 LUFS（与合成端对齐）

依赖（首次运行前装好）：
  /Users/yirongcao/.cache/modelscope/models/multimodalart--resemble-enhance-mlx/
  + soundfile + mlx + numpy + scipy

Resemble Enhance 模型来源：https://www.modelscope.cn/models/multimodalart/resemble-enhance-mlx
（HuggingFace CDN 在国内不稳，用 ModelScope）

性能：M4 Max 上 45s 输入约 19s 处理（RTF ~0.43×），一次性入库成本可接受。
"""
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


def _ffprobe_duration(wav_path: Path) -> float:
    ff = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(wav_path),
        ],
        capture_output=True, text=True, timeout=30,
    )
    try:
        return float(ff.stdout.strip())
    except ValueError:
        return 0.0


def _measure_noise_floor_db(wav_path: Path) -> float:
    """扫静音段，估底噪（dBFS）。

    用 silencedetect 找静音区间，再 volumedetect 那些区间的 RMS。
    返回噪声底噪 dBFS（典型 -50 ~ -30，越负越安静）。
    """
    # 先 volumedetect 整段
    ff = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(wav_path),
         "-af", "volumedetect",
         "-f", "null", "-"],
        capture_output=True, text=True, timeout=60,
    )
    full_rms = -60.0
    for line in ff.stderr.splitlines():
        m = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", line)
        if m:
            full_rms = float(m.group(1))
            break

    # silencedetect 取静音区间
    ff2 = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(wav_path),
         "-af", "silencedetect=noise=-35dB:d=0.3",
         "-f", "null", "-"],
        capture_output=True, text=True, timeout=60,
    )
    # 解析 silence_start/silence_end
    silences = []
    cur_start = None
    for line in ff2.stderr.splitlines():
        m = re.search(r"silence_start:\s*(-?[\d.]+)", line)
        if m:
            cur_start = float(m.group(1))
        m = re.search(r"silence_end:\s*(-?[\d.]+)", line)
        if m and cur_start is not None:
            silences.append((cur_start, float(m.group(1))))
            cur_start = None

    if not silences:
        # 没有静音段，假设底噪比整段 RMS 还要低 12 dB
        return max(full_rms - 12, -60)

    # 取最长的一段静音，用 atrim + volumedetect 量它
    longest = max(silences, key=lambda s: s[1] - s[0])
    s, e = longest
    ff3 = subprocess.run(
        ["ffmpeg", "-v", "info",
         "-ss", str(s), "-i", str(wav_path),
         "-t", str(e - s),
         "-af", "volumedetect",
         "-f", "null", "-"],
        capture_output=True, text=True, timeout=60,
    )
    for line in ff3.stderr.splitlines():
        m = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", line)
        if m:
            return float(m.group(1))

    return full_rms - 12


def _loudnorm_two_pass(
    wav_in: Path,
    wav_out: Path,
    target_lufs: float = -16.0,
    target_tp: float = -1.0,
    target_lra: float = 11.0,
) -> None:
    """EBU R128 两遍 loudnorm，命中率 ±0.2 LU。复用 voxcpm_tts 的实现。"""
    from voxcpm_tts import _loudnorm_two_pass as _impl
    _impl(wav_in, wav_out, target_lufs, target_tp, target_lra)


# ============ Resemble Enhance MLX (denoise + dereverb) ============

RESEMBLE_ENHANCE_PATH = os.environ.get(
    "RESEMBLE_ENHANCE_PATH",
    "/Users/yirongcao/.cache/modelscope/models/multimodalart--resemble-enhance-mlx/snapshots/master",
)
RESEMBLE_ENHANCE_WEIGHTS = os.path.join(RESEMBLE_ENHANCE_PATH, "resemble-enhance-mlx.safetensors")

_resemble_model = None


def _ensure_resemble_path():
    """把 Resemble Enhance 包目录加入 sys.path（一次性）。"""
    pkg = os.path.join(RESEMBLE_ENHANCE_PATH, "resemble_enhance_mlx")
    if os.path.isdir(pkg) and pkg not in __import__("sys").path:
        __import__("sys").path.insert(0, RESEMBLE_ENHANCE_PATH)


def _load_resemble_model(lambd: float = 0.5):
    """Lazy load Resemble Enhance MLX model. Cached after first load.

    Args:
        lambd: 0 = 跳过降噪（只增强），0.5 = 默认。1.0 = 最强降噪。
    """
    global _resemble_model
    if _resemble_model is not None:
        return _resemble_model
    if not os.path.exists(RESEMBLE_ENHANCE_WEIGHTS):
        raise FileNotFoundError(
            f"Resemble Enhance 权重缺失: {RESEMBLE_ENHANCE_WEIGHTS}。"
            f"从 modelscope 下载：modelscope download --model multimodalart/resemble-enhance-mlx"
        )
    _ensure_resemble_path()
    import resemble_enhance_mlx as re
    _resemble_model = re.load(RESEMBLE_ENHANCE_WEIGHTS, lambd=lambd)
    return _resemble_model


def _enhance_with_resemble(
    wav_in: Path,
    wav_out: Path,
    lambd: float = 0.5,
) -> None:
    """调用 Resemble Enhance MLX 做降噪 + 去混响。

    输入：任意采样率 wav（自动 resample 到 44.1kHz 内部处理）。
    输出：44.1kHz wav。
    """
    import soundfile as sf
    import numpy as np

    # 确保包目录在 sys.path（必须在 import 之前）
    _ensure_resemble_path()
    import resemble_enhance_mlx as re

    model = _load_resemble_model(lambd=lambd)
    wav, sr = sf.read(str(wav_in), dtype="float32")
    if wav.ndim > 1:
        wav = wav.mean(axis=1)
    out, out_sr = re.enhance(model, wav, sr)
    # 写 wav 留 24-bit 精度
    sf.write(str(wav_out), out, out_sr, subtype="PCM_16")


def clean_wav_for_cloning(
    wav_in: str,
    wav_out: str = None,
    noise_floor_db: float = None,
    use_resemble: bool = True,
    resemble_lambd: float = 0.5,
) -> dict:
    """
    把参考音频标准化成「干净、统一响度」的形式，适合 VoxCPM 克隆。

    Pipeline:
      ffmpeg preprocess → Resemble Enhance (ML 降噪 + 去混响) → loudnorm two-pass

    Args:
        wav_in: 输入 wav 路径
        wav_out: 输出路径（None → 写到 wav_in 同目录的 *.cleaned.wav）
        noise_floor_db: 备用，ffmpeg 降噪目标底噪（仅当 use_resemble=False 时用）
        use_resemble: True = 用 Resemble Enhance ML 模型（推荐）；
                      False = 退回 ffmpeg afftdn（无 ML 时兜底）
        resemble_lambd: 0=跳过降噪只增强；0.5=默认；1.0=最强降噪

    Returns:
        dict 含 {input_path, output_path, noise_floor_db, input_rms, output_rms,
                 resemble_used}
    """
    wav_in = Path(wav_in)
    if not wav_in.exists():
        raise FileNotFoundError(f"wav not found: {wav_in}")
    if wav_out is None:
        wav_out = wav_in.with_name(wav_in.stem + ".cleaned.wav")
    else:
        wav_out = Path(wav_out)

    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg 未找到")

    # 1) 测量原始电平
    ff = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(wav_in),
         "-af", "volumedetect",
         "-f", "null", "-"],
        capture_output=True, text=True, timeout=60,
    )
    input_rms = -60.0
    input_peak = -60.0
    for line in ff.stderr.splitlines():
        m = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", line)
        if m: input_rms = float(m.group(1))
        m = re.search(r"max_volume:\s*(-?[\d.]+)\s*dB", line)
        if m: input_peak = float(m.group(1))

    if noise_floor_db is None:
        noise_floor_db = _measure_noise_floor_db(wav_in)

    tmp_dir = Path(tempfile.mkdtemp(prefix="voxcpm_clean_"))
    try:
        preprocessed = tmp_dir / "preprocessed.wav"
        enhanced = tmp_dir / "enhanced.wav"
        loudnormed = tmp_dir / "loudnormed.wav"

        # 2) ffmpeg 预处理：频谱边缘裁剪
        # highpass=80: 去 HVAC/电流嗡声
        # lowpass=12000: 去高频嘶声（人声 12kHz 以上基本无内容）
        # -ar 48000: 锁采样率（afftdn/Resemble Enhance 都不需要更高）
        prep_filter = "highpass=f=80,lowpass=f=12000"
        ff1 = subprocess.run(
            ["ffmpeg", "-y", "-v", "info",
             "-i", str(wav_in),
             "-af", prep_filter,
             "-ar", "48000",
             str(preprocessed)],
            capture_output=True, text=True, timeout=180,
        )
        if ff1.returncode != 0:
            raise RuntimeError(f"preprocess 失败: {ff1.stderr[-300:]}")

        resemble_used = False
        if use_resemble and os.path.exists(RESEMBLE_ENHANCE_WEIGHTS):
            # 3a) Resemble Enhance ML 模型：降噪 + 去混响（ffmpeg 做不到的部分）
            try:
                _enhance_with_resemble(preprocessed, enhanced, lambd=resemble_lambd)
                resemble_used = True
            except Exception as e:
                # 模型失败回退到 ffmpeg，不阻塞用户保存
                import warnings
                warnings.warn(f"Resemble Enhance 失败，回退到 ffmpeg afftdn: {e}")
                use_resemble = False

        if not resemble_used:
            # 3b) 兜底：ffmpeg afftdn（仅降噪，无去混响）
            denoise_filter = (
                f"afftdn=nf={noise_floor_db}:tn=1"
            )
            ff2 = subprocess.run(
                ["ffmpeg", "-y", "-v", "info",
                 "-i", str(preprocessed),
                 "-af", denoise_filter,
                 str(enhanced)],
                capture_output=True, text=True, timeout=180,
            )
            if ff2.returncode != 0:
                raise RuntimeError(f"ffmpeg denoise 失败: {ff2.stderr[-300:]}")

        # 4) 两遍 loudnorm 归一化到 -16 LUFS
        _loudnorm_two_pass(enhanced, loudnormed)

        # 5) 复制到输出
        shutil.copy2(str(loudnormed), str(wav_out))

        # 6) 量输出
        ff3 = subprocess.run(
            ["ffmpeg", "-v", "info", "-i", str(wav_out),
             "-af", "volumedetect",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=60,
        )
        output_rms = -60.0
        for line in ff3.stderr.splitlines():
            m = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", line)
            if m: output_rms = float(m.group(1))

        return {
            "input_path": str(wav_in),
            "output_path": str(wav_out),
            "input_rms": input_rms,
            "input_peak": input_peak,
            "output_rms": output_rms,
            "noise_floor_db": noise_floor_db,
            "resemble_used": resemble_used,
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def analyze_voice(wav_path: str) -> dict:
    """分析参考音频的噪声/混响/电平特征，用于诊断是否需要清洗。"""
    wav_path = Path(wav_path)
    if not wav_path.exists():
        raise FileNotFoundError(f"wav not found: {wav_path}")

    info = {"path": str(wav_path), "duration": _ffprobe_duration(wav_path)}

    # 全段 volumedetect
    ff = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(wav_path),
         "-af", "volumedetect",
         "-f", "null", "-"],
        capture_output=True, text=True, timeout=60,
    )
    for line in ff.stderr.splitlines():
        m = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", line)
        if m: info["rms_db"] = float(m.group(1))
        m = re.search(r"max_volume:\s*(-?[\d.]+)\s*dB", line)
        if m: info["peak_db"] = float(m.group(1))

    info["crest_db"] = info.get("peak_db", -60) - info.get("rms_db", -60)
    info["noise_floor_db"] = _measure_noise_floor_db(wav_path)
    info["snr_estimate_db"] = info.get("rms_db", -60) - info["noise_floor_db"]

    # LRA
    ff2 = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(wav_path),
         "-af", "loudnorm=I=-16:TP=-1.0:LRA=11:print_format=json",
         "-f", "null", "-"],
        capture_output=True, text=True, timeout=60,
    )
    jstart = ff2.stderr.find("{")
    jend = ff2.stderr.rfind("}") + 1
    try:
        import json
        s = json.loads(ff2.stderr[jstart:jend])
        info["lra"] = float(s["input_lra"])
    except Exception:
        info["lra"] = 0

    # 诊断
    flags = []
    if info.get("crest_db", 12) < 8:
        flags.append("reverb_likely")  # crest < 8 → 录音被混响"涂"平
    if info.get("snr_estimate_db", 60) < 25:
        flags.append("noisy")  # SNR < 25dB → 环境噪声明显
    if info.get("rms_db", -30) < -25:
        flags.append("low_level")  # RMS < -25 → 录音电平偏低
    if info.get("peak_db", -1) > -1:
        flags.append("near_clipping")  # peak > -1dBFS → 接近削波
    info["flags"] = flags
    return info