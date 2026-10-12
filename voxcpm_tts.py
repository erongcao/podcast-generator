#!/usr/bin/env python3
"""
本地 VoxCPM2 语音克隆适配器

VoxCPM2 是 openbmb（清华 CMI）出的 2B 多语种 TTS，多码流架构（非纯 AR）。
优势：
  - 终止符学习更稳（diffusion-style steps），不会有 Qwen3-TTS 那种"短 target 重复/乱码"问题
  - ref_text 可选——只要 ref_audio 就能克隆，不需要每段都填转写
  - 30 种语言含中文
  - 48kHz studio 质量输出

调用方式：mlx-audio 加载 mlx-community/VoxCPM2-8bit，model.generate() 直接出 mx.array。
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import numpy as np
import soundfile as sf

from exceptions import AudioProcessingError


def _require_mlx_audio():
    """Lazy import guard for mlx_audio.

    报错信息必须可执行：用户不应该看到裸 ModuleNotFoundError。
    mlx-audio 只在 Apple Silicon + Python 3.10+ 能装（MLX 框架约束）。
    """
    try:
        import mlx_audio  # noqa: F401
    except ImportError as e:
        raise AudioProcessingError(
            "VoxCPM2 需要 mlx-audio，但当前 Python 环境装不到。\n"
            f"  当前 Python: {sys.executable}\n"
            f"  原始错误: {e}\n"
            "修复：用项目 venv 启动 GUI —— bin/podcast-gui.sh\n"
            "或在当前环境跑：pip install mlx-audio\n"
            "（注：mlx-audio 仅支持 Apple Silicon Mac + Python 3.10+）"
        ) from e

VOXCPM_MODEL_PATH = os.environ.get(
    "VOXCPM_MODEL_PATH",
    "/Users/yirongcao/.cache/modelscope/models/mlx-community--VoxCPM2-bf16/snapshots/master",
)


def _require_model():
    model_dir = Path(VOXCPM_MODEL_PATH)
    if not model_dir.exists():
        raise AudioProcessingError(
            f"VoxCPM2 模型目录不存在: {model_dir}。"
        )
    # 支持单文件 (8-bit) 或分片 (bf16: model-00001.safetensors + index)
    has_single = (model_dir / "model.safetensors").exists()
    has_shard_index = (model_dir / "model.safetensors.index.json").exists()
    has_shard_files = any(model_dir.glob("model-*.safetensors"))
    if not (has_single or (has_shard_index and has_shard_files)):
        raise AudioProcessingError(
            f"VoxCPM2 模型权重缺失：{model_dir} 下既无 model.safetensors，"
            f"也无 model-*.safetensors + index。请检查路径或重新下载。"
        )


def _load_model():
    """Lazy load mlx-audio VoxCPM2 model. Cached after first load.

    Requires the **editable** mlx-audio install at
    ``~/.podcast-automation/mlx-audio/`` (not the PyPI 0.2.x package
    that pip dropped into ``~/.podcast-automation/.venv/``). The
    editable source ships ``mlx_audio/tts/models/voxcpm2/`` natively
    and has ``voxcpm2`` in MODEL_REMAPPING — no patches needed.

    Launcher ``bin/podcast-gui.sh`` is configured to use the correct
    venv; do not run the GUI from a different python or this import
    will hit the v1-only PyPI package and the "Model type voxcpm2
    not supported" error returns.
    """
    global _model
    if _model is None:
        _require_model()
        _require_mlx_audio()
        from mlx_audio.tts.utils import load_model
        _model = load_model(Path(VOXCPM_MODEL_PATH))
    return _model


def _loudnorm_two_pass(
    wav_in: Path,
    wav_out: Path,
    target_lufs: float = -16.0,
    target_tp: float = -1.0,
    target_lra: float = 11.0,
) -> None:
    """EBU R128 两遍 loudnorm，多 voice 音量对齐（Apple Podcasts 标准）。

    Args:
        target_lufs: 目标综合响度（-16 LUFS 是 podcast 标准，小宇宙/喜马拉雅通用）
        target_tp: 目标真峰值（-1.0 dBTP 是 Apple Podcasts 规范；-1.5 太紧会触发
                    limiter 削减增益，导致各 voice 受限不同反而拉不开差距）
        target_lra: 目标响度范围（11 LU 适合人声为主的播客）

    Pass 1：dry run + print_format=json，提取 input_i / input_tp / input_lra / input_thresh
    Pass 2：linear=true 带 measured_* 重跑，输出尽量命中目标 LUFS

    实测：4 voice 输入下输出 LUFS 极差从 ±1.6 LU（单遍）压到 ±1.0 LU（两遍）。
    短段（<5s）linear 模式也受 TP limiter 影响，±0.5 LU 是 ffmpeg 上限。

    注意：此函数用于 per-seg 路径（已废弃）；master 路径请用 _master_loudnorm_mp3。
    原因：per-seg 短段（<5s）的 integrated loudness 受单段内容影响大，
    1 段"啊！"被压到 -16 LUFS，与 10 秒平静句同响度，听感"忽轻忽响"。
    """
    import json

    if not shutil.which("ffmpeg"):
        raise AudioProcessingError("ffmpeg 未找到，无法 loudnorm")

    # Pass 1：测量。注意 loudnorm JSON 用 AV_LOG_INFO 输出，-v error 会屏蔽，必须 -v info
    p1 = subprocess.run(
        [
            "ffmpeg", "-y", "-v", "info",
            "-i", str(wav_in),
            "-af",
            f"loudnorm=I={target_lufs}:TP={target_tp}:LRA={target_lra}"
            f":print_format=json",
            "-f", "null", "-",
        ],
        capture_output=True, text=True, timeout=120,
    )
    jstart = p1.stderr.rfind("{")
    jend = p1.stderr.rfind("}") + 1
    if jstart < 0 or jend <= jstart:
        raise AudioProcessingError(
            f"loudnorm pass1 没产出 stats: stderr={p1.stderr[-300:]}"
        )
    try:
        stats = json.loads(p1.stderr[jstart:jend])
    except json.JSONDecodeError as e:
        raise AudioProcessingError(f"loudnorm pass1 JSON 解析失败: {e}; raw={p1.stderr[jstart:jend][:300]}")

    # Pass 2：linear 应用
    linear_filter = (
        f"loudnorm=I={target_lufs}:TP={target_tp}:LRA={target_lra}"
        f":measured_I={stats['input_i']}"
        f":measured_TP={stats['input_tp']}"
        f":measured_LRA={stats['input_lra']}"
        f":measured_thresh={stats['input_thresh']}"
        f":offset={stats['target_offset']}"
        f":linear=true:print_format=summary"
    )
    p2 = subprocess.run(
        [
            "ffmpeg", "-y", "-v", "info",
            "-i", str(wav_in),
            "-af", linear_filter,
            "-ar", "48000",  # 锁采样率，防止上游 filter 升采样导致文件膨胀
            str(wav_out),
        ],
        capture_output=True, text=True, timeout=120,
    )
    if p2.returncode != 0:
        raise AudioProcessingError(
            f"loudnorm pass2 失败: {p2.stderr[-300:]}"
        )


def _master_loudnorm_mp3(
    mp3_in: str,
    mp3_out: str,
    target_lufs: float = -16.0,
    target_tp: float = -1.0,
    target_lra: float = 11.0,
) -> dict:
    """
    整 podcast master loudnorm → MP3 320k / 48kHz / mono。

    替代 per-segment loudnorm（2026-10-11 改）：
    per-seg 短段 < 5s 的 integrated loudness 受单段内容影响极大（短喊叫 vs 长平静），
    每段都压到 -16 LUFS 后段间差 ±3-6 dB，听感"忽轻忽响"。
    master 走整 podcast integrated window（典型 30s+），±1 LU 命中，
    段间相对响度由人声天然强弱决定，**听感更自然**。

    Returns: dict with measured input/output LUFS / TP（用于日志/调试）
    """
    import json

    if not shutil.which("ffmpeg"):
        raise AudioProcessingError("ffmpeg 未找到，无法 loudnorm")

    # Pass 1：测量整 podcast integrated loudness
    p1 = subprocess.run(
        [
            "ffmpeg", "-y", "-v", "info",
            "-i", str(mp3_in),
            "-af",
            f"loudnorm=I={target_lufs}:TP={target_tp}:LRA={target_lra}"
            f":print_format=json",
            "-f", "null", "-",
        ],
        capture_output=True, text=True, timeout=180,
    )
    jstart = p1.stderr.rfind("{")
    jend = p1.stderr.rfind("}") + 1
    if jstart < 0 or jend <= jstart:
        raise AudioProcessingError(
            f"master loudnorm pass1 没产出 stats: stderr={p1.stderr[-300:]}"
        )
    try:
        stats = json.loads(p1.stderr[jstart:jend])
    except json.JSONDecodeError as e:
        raise AudioProcessingError(
            f"master loudnorm pass1 JSON 解析失败: {e}; raw={p1.stderr[jstart:jend][:300]}"
        )

    # Pass 2：linear 应用 + 编码 MP3
    linear_filter = (
        f"loudnorm=I={target_lufs}:TP={target_tp}:LRA={target_lra}"
        f":measured_I={stats['input_i']}"
        f":measured_TP={stats['input_tp']}"
        f":measured_LRA={stats['input_lra']}"
        f":measured_thresh={stats['input_thresh']}"
        f":offset={stats['target_offset']}"
        f":linear=true:print_format=summary"
    )
    p2 = subprocess.run(
        [
            "ffmpeg", "-y", "-v", "info",
            "-i", str(mp3_in),
            "-af", linear_filter,
            "-ar", "48000", "-ac", "1", "-ab", "320k",
            str(mp3_out),
        ],
        capture_output=True, text=True, timeout=180,
    )
    if p2.returncode != 0:
        raise AudioProcessingError(
            f"master loudnorm pass2 失败: {p2.stderr[-300:]}"
        )

    # 解析 pass2 summary，提取 input/output LUFS 报告
    out = {
        "input_lufs": stats.get("input_i"),
        "input_tp": stats.get("input_tp"),
        "input_lra": stats.get("input_lra"),
        "target_lufs": target_lufs,
    }
    for line in p2.stderr.splitlines():
        m = re.search(r"Output Integrated:\s*(-?[\d.]+)\s*LUFS", line)
        if m:
            out["output_lufs"] = float(m.group(1))
        m = re.search(r"Output True Peak:\s*(-?[\d.]+)\s*dBTP", line)
        if m:
            out["output_tp"] = float(m.group(1))
    return out


def _band_rms_db(wav_path: Path, lpf: int, hpf: int) -> float:
    """测 wav_path 在 [hpf, lpf] Hz 频段的 RMS（dB）。"""
    ff = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(wav_path),
         "-af", f"lowpass=f={lpf},highpass=f={hpf},volumedetect",
         "-f", "null", "-"],
        capture_output=True, text=True, timeout=60,
    )
    m = re.search(r"mean_volume:\s*(-?[\d.]+)", ff.stderr)
    return float(m.group(1)) if m else -60.0


# 缓存：wav 路径 + mtime → ref audio tilt（dB）。同 wav 不重复测量。
_tilt_cache: dict = {}

# tilt > 此阈值 → 启用 Resemble Enhance；否则跳过（女声等干净音色）。
# 阈值依据：实测 5 个音色，3 个男声 tilt 11-13 dB，2 个女声 5-6 dB。
_TILT_RESEMBLE_THRESHOLD_DB = 10.0


def _measure_ref_tilt(wav_path: Path) -> float:
    """测 ref audio 低高频段 tilt（low_db - high_db，正值=偏闷）。

    用于判断该音色是否需要 Resemble Enhance 后处理。VoxCPM2 忠实地克隆
    ref audio 的频谱偏置——男声基频/胸腔共鸣导致 ref 在 20-200Hz 比女声
    高 6+ dB，输出继承此 tilt。

    实测 podcast_20260925_185417_f5520a.mp3：
      Li-test 输出 tilt 13 dB（闷），jiang-test 输出 5 dB（自然）。
    Resemble Enhance λ=0.5 可把 13 dB → 9.2 dB（对齐 MiniMax）。

    Args:
        wav_path: ref audio 路径（优先 cleaned.wav，未清洗时用 raw）

    Returns:
        tilt_db: 正=低频隆起（需要 Resemble），≤10=干净（跳过 Resemble）
    """
    wav_path = Path(wav_path)
    key = (str(wav_path), wav_path.stat().st_mtime)
    if key in _tilt_cache:
        return _tilt_cache[key]
    low = _band_rms_db(wav_path, 200, 20)
    high = _band_rms_db(wav_path, 8000, 2000)
    tilt = low - high  # 正=低频隆起
    _tilt_cache[key] = tilt
    return tilt


def _needs_resemble(tilt_db: float, tags: list[str] | None = None) -> bool:
    """判定是否对合成输出跑 Resemble Enhance MLX。

    优先级：tag 优先于 tilt 自动判定。
    - tag 含 "female" 或 "no_enhance" → False（用户明确不要后处理）
    - 其他情况按 tilt_db > 阈值判定（默认 10 dB，男声低频隆起型）

    留空 tags 时等价于 []，走纯 tilt 判定（向后兼容）。
    """
    tags = list(tags or [])
    if "female" in tags or "no_enhance" in tags:
        return False
    return tilt_db > _TILT_RESEMBLE_THRESHOLD_DB


_model = None


def synthesize(
    text: str,
    wav_path: str,
    ref_text: str = None,
    output_format: str = "mp3",
    cfg_value: float = 3.0,
    inference_timesteps: int = 30,
    warmup_patches: int = 2,
    speed: float = 1.0,
    use_resemble: bool = False,
) -> bytes:
    """
    用 VoxCPM2 克隆 wav_path 的音色，合成 text，返回 mp3 bytes。

    Args:
        text: 待合成文本
        wav_path: 参考音频（推荐 10-30s 单人独白）
        ref_text: 可选；不提供时 VoxCPM2 仅用 ref_audio 编码
        output_format: "mp3"（默认）或 "wav"
        cfg_value: 分类器自由引导强度。**默认 3.0**（极致音质档）。
               官方默认 2.0 是强制最小值，CLI 会强制 max(cfg_scale, 2.0)。
               调高增强 voice cloning 约束，过大会失真。
        inference_timesteps: diffusion 步数。**默认 30**（极致音质档）。
               官方默认 10 = 最低推荐值；README 原话"Lower = faster, higher = better quality"。
               调高音质明显提升，但 RTF 线性变慢（30 步 ≈ 10 步的 3×）。
        warmup_patches: 在输出前额外生成 N 个 patches（不计入输出）。
               **默认 2**（极致音质档）；消除短段开头偶尔的"噗"声/电流感。
               官方默认 0。
        speed: 语速倍数（0.7-1.5；1.0=不变速）。走 ffmpeg atempo 后处理，
               与 VoxCPM 自身参数无关，是变速而非变调。
        use_resemble: 是否对合成输出跑 Resemble Enhance MLX（降噪+去混响）。
               **默认 False**。由 `synthesize_segments()` 按 ref audio tilt 自动决定：
               男声 tilt > 10 dB → True；女声 tilt ≤ 10 → False。
               λ=0.5 实测把 tilt 从 13 dB → 9.2 dB（对齐 MiniMax speech-HD）。

    Note:
        - 多个连续 atempo 滤镜可串 0.5-2.0 全范围；这里只用 1 个 atempo
          覆盖 0.7-1.5（GUI 滑块范围），更稳。
        - 2026-09-25 极致音质档：30/3.0/2（vs 官方默认 10/2.0/0），
          解决 VoxCPM 输出相对 MiniMax speech-HD 的清晰度/底噪差距。
        - 2026-09-25 加 Resemble Enhance：补偿 VoxCPM2 AudioVAE V2
          对男声低频偏置的"忠实克隆"，仅 tilt > 10 的音色启用。
    """
    wav_path = Path(wav_path)
    if not wav_path.exists():
        raise AudioProcessingError(f"参考音频不存在: {wav_path}")
    if not text or not text.strip():
        raise AudioProcessingError("text 不能为空")

    model = _load_model()
    tmp_dir = Path(tempfile.mkdtemp(prefix="voxcpm_"))
    try:
        prefix = tmp_dir / "out"
        wav_out = tmp_dir / "out.wav"  # 单段 mlx_audio 写 {prefix}.{format}
        mp3_out = tmp_dir / "out.mp3"
        sped_out = tmp_dir / "sped.wav"  # 变速后中间文件
        enh_out = tmp_dir / "enh.wav"  # Resemble Enhance 后中间文件
        norm_out = tmp_dir / "norm.wav"  # 音量归一化后中间文件

        # model.generate 是 generator；只产 1 个 GenerationResult
        kwargs = dict(
            text=text,
            ref_audio=str(wav_path),
            cfg_value=cfg_value,
            inference_timesteps=inference_timesteps,
            warmup_patches=warmup_patches,
        )
        if ref_text:
            kwargs["ref_text"] = ref_text
        result = next(model.generate(**kwargs))

        # mlx-audio ≥0.2.0 移除了 mlx_audio.audio_io；用 soundfile 直写
        # （soundfile 是 mlx-audio 的依赖，必然已装）
        wav_target = prefix.with_suffix(".wav")
        sf.write(str(wav_target), np.asarray(result.audio), result.sample_rate, format="wav")

        if not wav_out.exists():
            candidate = tmp_dir / "out_000.wav"
            if candidate.exists():
                wav_out = candidate

        if not shutil.which("ffmpeg"):
            raise AudioProcessingError("ffmpeg 未找到，无法转 mp3")

        # 1) 语速调整（ffmpeg atempo；保留采样率/声道）
        if abs(speed - 1.0) < 0.01:
            sped_in = wav_out
        else:
            ff_speed = subprocess.run(
                [
                    "ffmpeg", "-y", "-v", "error",
                    "-i", str(wav_out),
                    "-filter:a", f"atempo={speed}",
                    str(sped_out),
                ],
                capture_output=True, text=True, timeout=60,
            )
            if ff_speed.returncode != 0:
                raise AudioProcessingError(
                    f"ffmpeg atempo 变速失败: {ff_speed.stderr[-300:]}"
                )
            sped_in = sped_out

        # 1.5) Resemble Enhance MLX 降噪 + 去混响（仅对 tilt > 10 的音色启用）
        # 解决 VoxCPM2 AudioVAE V2 对男声低频偏置的"忠实克隆"。
        # 实测：男声（Li-test）tilt 13 → 9.2 dB（对齐 MiniMax 8.6 dB）。
        # 女声（jiang-test）tilt ~5 dB 已自然，跳过省算力。
        # 失败兜底：Resemble 失败时打印 warning 并继续用 sped_in（不阻塞女主）。
        # 注：Resemble 必须在 loudnorm **之前**，因为它会改电平；
        #     loudnorm 会把 enh.wav 重采样到 48kHz（自带 -ar 48000）。
        if use_resemble:
            try:
                from audio_cleanup import _enhance_with_resemble
                _enhance_with_resemble(sped_in, enh_out, lambd=0.5)
                sped_in = enh_out
            except Exception as e:
                import warnings
                warnings.warn(
                    f"voxcpm_tts.synthesize: Resemble Enhance 失败，跳过: {e}"
                )

        # 2) Per-segment loudnorm (restored 2026-10-12)
        # 原因: VoxCPM2 diffusion sampling 会放大参考音频的局部能量峰值。
        # jiangyu-4 录音本身有强弱表情（"声情并茂"），合成的段内 peak spread 高达 14 dB。
        # 每段拉到 -16 LUFS / -1.0 dBTP，保证段间 peak spread ≤2 dB；
        # 行为对齐 MiniMax cloud TTS（其内部 peak limiter 实测 -1.0 dB / 段间 spread 2.2 dB）。
        # Master loudnorm 已不再需要，见 synthesize_segments() 末尾（原 _master_loudnorm_mp3 处已废弃）。
        norm_out = tmp_dir / "norm.wav"
        try:
            _loudnorm_two_pass(sped_in, norm_out, target_lufs=-16.0, target_tp=-1.0, target_lra=11.0)
            loudnormed_wav = norm_out
        except Exception as e:
            # loudnorm 是 best-effort；失败时退回 raw wav，不阻塞整次合成
            _log(f"per-segment loudnorm 失败 (seg {i}): {e}; 退回 raw wav")
            loudnormed_wav = sped_in

        # 3) wav→目标格式
        if output_format == "wav":
            return loudnormed_wav.read_bytes()

        ff = subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error",
                "-i", str(loudnormed_wav),
                # 48kHz 保留女声 8-12kHz 气声频段（之前 -ar 24000 砍掉导致女声浑浊）
                "-ar", "48000",
                "-ac", "1",
                # mp3 320k 是 mp3 编码上限，微信公众号全版本兼容
                "-ab", "320k",
                str(mp3_out),
            ],
            capture_output=True, text=True, timeout=60,
        )
        if ff.returncode != 0:
            raise AudioProcessingError(
                f"ffmpeg wav→mp3 失败: {ff.stderr[-300:]}"
            )
        return mp3_out.read_bytes()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _resolve_wav(voice_id: str):
    import voice_library as vl
    from exceptions import VoiceCloneError

    record = vl.get(voice_id)
    if record is None:
        raise VoiceCloneError(f"音色库中不存在 voice_id={voice_id}")
    raw_path = record.get("qwen3_wav_path") or record.get("wav_path")
    if not raw_path:
        raise VoiceCloneError(f"voice_id={voice_id} 缺 wav_path")
    # 优先用清洗过的版本（audio_cleanup.clean_wav_for_cloning 产出）
    # cleaned.wav 与原 wav 同目录、同 stem，采样率 48kHz、-16 LUFS
    # 不同 voice 组合时不会出现「底噪/混响/电平不一致」问题
    cleaned_path = Path(raw_path).with_name(
        Path(raw_path).stem + ".cleaned.wav"
    )
    wav_path = str(cleaned_path) if cleaned_path.exists() else raw_path
    return wav_path, (record.get("ref_text") or "").strip() or None


def synthesize_segments(
    segments: list,
    voice_id: str = None,
    progress_callback=None,
    output_path: str = None,
    cfg_value: float = 3.0,
    inference_timesteps: int = 30,
    warmup_patches: int = 2,
    speaker_map: dict = None,
    speed_map: dict = None,
    on_log=None,
):
    """
    多段文本合成（与 minimax_tts.synthesize_segments 同形）。

    Args:
        segments: [{"speaker": str, "text": str}, ...]
        voice_id: 兜底 voice_id（speaker_map 未命中时回退；旧 API 兼容）
        progress_callback: callable(done, total)
        output_path: 最终合并 mp3 路径
        cfg_value: 引导强度（默认 3.0，极致音质档）
        inference_timesteps: diffusion 步数（默认 30，极致音质档）
        warmup_patches: 输出前额外 patches（默认 2，极致音质档）
        speaker_map: 角色标签 → voice_id 映射（如 {"主播甲": "Li-test-v1"}）。
                     命中时用对应 voice；未命中回退到 voice_id。
        speed_map: 角色标签 → 语速（0.5-1.5）映射。未命中回退到 1.0。
        on_log: callable(str) — 段级日志（"合成段 1/N..."），让用户看到动起来，
                避免 1 段脚本时进度条卡在 0% 让人误以为程序死了。

    Returns:
        (output_path, order_desc)
    """
    from exceptions import VoiceCloneError
    def _log(msg: str):
        if on_log:
            on_log(msg)

    if not segments:
        raise AudioProcessingError("segments 为空")
    if output_path is None:
        raise AudioProcessingError("必须指定 output_path")
    if not voice_id and not speaker_map:
        raise AudioProcessingError("必须指定 voice_id 或 speaker_map")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    seg_dir = output_path.parent / f"voxcpm_seg_{uuid.uuid4().hex[:6]}"
    seg_dir.mkdir(parents=True, exist_ok=True)

    # 解析每个 speaker 的 wav_path（缓存避免重复 IO）
    wav_cache: dict = {}  # voice_id → (wav_path, ref_text)

    def _wav_for(vid):
        if vid not in wav_cache:
            wav_cache[vid] = _resolve_wav(vid)
        return wav_cache[vid]

    seg_files = []
    order_desc = []
    total = sum(1 for s in segments if s.get("text", "").strip())
    done = 0

    # 启动信号：让 GUI 立刻从 0% 走到当前 0/N，并打印"开始"
    if progress_callback:
        progress_callback(0, max(total, 1))
    _log(f"VoxCPM 合成开始：共 {total} 段，输出 {output_path.name}")
    for i, seg in enumerate(segments):
        text = (seg.get("text") or "").strip()
        speaker = seg.get("speaker", "") or f"seg{i}"
        if not text:
            continue

        # 选 voice（speaker 去首尾空白，防御 caller 传脏字符串）
        speaker = speaker.strip()
        chosen_vid = (speaker_map or {}).get(speaker, "") or voice_id or ""
        if not chosen_vid:
            raise AudioProcessingError(
                f"段 {i} ({speaker}) 没找到对应 voice（speaker_map 未命中且无兜底 voice_id）"
            )
        wav_path, ref_text = _wav_for(chosen_vid)
        # 读取该 voice 的 tags（用户打的 female/no_enhance 等）
        import voice_library as vl  # 同 _resolve_wav，独立 import 避免作用域问题
        _vl_tags = (vl.get(chosen_vid) or {}).get("tags") or []

        # 选语速
        speed = (speed_map or {}).get(speaker, 1.0)

        # 选是否走 Resemble Enhance：tag 优先 > tilt 自动判定
        # - tag "female" / "no_enhance" → 跳过（用户明确跳过）
        # - tilt > 10 → 走 Resemble（男声低频隆起型）
        # 测一次缓存，后续同音色直接读缓存。
        tilt = _measure_ref_tilt(Path(wav_path))
        use_resemble = _needs_resemble(tilt, _vl_tags)

        suffix = ""
        if use_resemble:
            suffix = "*,RE"
        elif "female" in _vl_tags:
            suffix = ",F"
        elif "no_enhance" in _vl_tags:
            suffix = ",N"
        order_desc.append(f"{speaker}({chosen_vid[:12]},tilt={tilt:.1f}{suffix})")
        seg_mp3 = seg_dir / f"{i:03d}_{re.sub(r'[^a-zA-Z0-9_-]', '_', speaker)[:20]}.mp3"
        # 段级日志：让用户在 1 段脚本时也能看到"动起来"
        preview = text[:30] + ("…" if len(text) > 30 else "")
        _log(
            f"合成段 {i+1}/{total} [{speaker} → {chosen_vid[:16]}]"
            f" speed={speed:.2f}x tilt={tilt:.1f}dB{suffix} : {preview}"
        )
        try:
            mp3_bytes = synthesize(
                text=text,
                wav_path=wav_path,
                ref_text=ref_text,
                cfg_value=cfg_value,
                inference_timesteps=inference_timesteps,
                warmup_patches=warmup_patches,
                speed=speed,
                use_resemble=use_resemble,
            )
            seg_mp3.write_bytes(mp3_bytes)
        except VoiceCloneError:
            raise
        except Exception as e:
            raise AudioProcessingError(f"段 {i} ({speaker}) 合成失败: {e}")
        seg_files.append(str(seg_mp3))
        done += 1
        if progress_callback:
            progress_callback(done, total)

    _concat_mp3(str(output_path), seg_files)

    # Master loudnorm 已废弃 (2026-10-12): 改用 per-segment loudnorm
    # （见 synthesize() 内的 _loudnorm_two_pass 调用）.
    # 整 podcast 的段间 LUFS / peak 已在段级归一，这里 _concat_mp3 已直接
    # 把 concat 结果写到 output_path，不需要再 post-process。

    return str(output_path), " → ".join(order_desc)


def _concat_mp3(output_path: str, mp3_files: list) -> None:
    if not mp3_files:
        return
    if len(mp3_files) == 1:
        Path(output_path).write_bytes(Path(mp3_files[0]).read_bytes())
        return
    list_file = Path(output_path).with_suffix(".concat.txt")
    try:
        # 用绝对路径！ffmpeg 的 concat demuxer 把 list_file 里的相对路径
        # 解析为「相对于 list_file 所在目录」，而 list_file 在 output/、
        # mp3_files 路径也以 output/ 开头 → 拼成 output/output/... 不存在。
        # 用绝对路径避开一切相对路径歧义。
        abs_files = [str(Path(p).resolve()) for p in mp3_files]
        list_file.write_text(
            "\n".join(f"file '{p}'" for p in abs_files),
            encoding="utf-8",
        )
        ff = subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error",
                "-f", "concat", "-safe", "0",
                "-i", str(list_file.resolve()),
                "-c", "copy",
                str(Path(output_path).resolve()),
            ],
            capture_output=True, text=True, timeout=120,
        )
        if ff.returncode != 0:
            raise AudioProcessingError(
                f"ffmpeg concat 失败: {ff.stderr[-300:]}"
            )
    finally:
        list_file.unlink(missing_ok=True)