#!/usr/bin/env python3
"""
MiniMax TTS 语音合成模块
支持预设音色、复刻音色、多音色交替合成

优先使用 mmx CLI，fallback 到 API

MiniMax T2A v2 (/v1/t2a_v2) 验证过的端点：
  POST https://api.minimaxi.com/v1/t2a_v2
    Body: {"model": "speech-2.8-hd|turbo|...", "text": "...",
           "voice_setting": {"voice_id", "speed", "vol", "pitch", "emotion"},
           "audio_setting": {"sample_rate", "bitrate", "format", "channel"},
           "output_format": "hex|url",
           "language_boost": "auto|Chinese|English|...",
           "subtitle_enable": false|true,
           "subtitle_type": "sentence|word"}
    Response: {"data": {"audio": "<hex>|<url>", "subtitle_file": "<oss-url>",
                        "status": <int>, "ced": ""},
               "extra_info": {"audio_length": <ms>, "audio_sample_rate": <hz>,
                              "audio_size": <bytes>, ...},
               "trace_id": "...", "base_resp": {"status_code": 0, ...}}

字幕端点（关键发现 - 通过实测验证）：
  字幕不在 data.audio 中！API 在 subtitle_enable=true 时返回 data.subtitle_file，
  这是一个 OSS 预签名 URL（不是 file_id）。直接 GET 即可获得字幕 JSON。
  字幕 JSON 形如：[{"text": "...", "time_begin": <ms>, "time_end": <ms>,
                    "timestamped_words": [{word, time_begin, time_end}]}]。
  注意时间单位是毫秒，不是秒。
"""

import binascii
import json
import os
import shutil
import subprocess
import tempfile
from typing import Iterator, List, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import CONFIG
from exceptions import APIError, AudioProcessingError
from logger import MiniMax
from network_retry import retry_on_transient
# 复用 voxcpm_tts 的 master loudnorm（2026-10-11 加；之前 minimax 根本没归一化）
from voxcpm_tts import _master_loudnorm_mp3

DEFAULT_MODEL = "speech-2.8-hd"
DEFAULT_SAMPLE_RATE = 32000
DEFAULT_FORMAT = "mp3"

# 支持的模型列表（用于 UI 选择器；按"较新在前"排序）
SUPPORTED_MODELS = [
    "speech-2.8-hd",
    "speech-2.8-turbo",
    "speech-2.6-hd",
    "speech-2.6-turbo",
    "speech-02-hd",
    "speech-02-turbo",
    "speech-01-hd",
    "speech-01-turbo",
]

# 支持的音频格式（loudnorm 仅兼容 mp3；其他格式输出跳过音量标准化）
SUPPORTED_FORMATS = ["mp3", "pcm", "flac", "wav", "pcmu_raw", "pcmu_wav", "opus"]

# 支持的 language_boost 值（最常用的子集）
SUPPORTED_LANGUAGES = [
    "auto",
    "Chinese",
    "Chinese,Yue",
    "English",
    "Japanese",
    "Korean",
    "Spanish",
    "French",
    "German",
    "Russian",
    "Arabic",
    "Portuguese",
    "Vietnamese",
    "Thai",
    "Indonesian",
    "Italian",
]

# emotion 可选值（从 speech-2.8 文档摘录；UI 默认 calm）
SUPPORTED_EMOTIONS = ["happy", "sad", "angry", "fearful", "disgusted", "surprised", "neutral", "calm", "fluent", "whisper"]


def get_supported_models() -> list[str]:
    return list(SUPPORTED_MODELS)


def get_supported_formats() -> list[str]:
    return list(SUPPORTED_FORMATS)


def get_supported_languages() -> list[str]:
    return list(SUPPORTED_LANGUAGES)


def get_supported_emotions() -> list[str]:
    return list(SUPPORTED_EMOTIONS)


# 带指数退避重试的 Session，处理瞬时网络错误（RemoteDisconnected、ConnectionError、Timeout）
_RETRY = Retry(
    total=3,
    backoff_factor=1.0,  # 第 1 次重试 1s，第 2 次 2s，第 3 次 4s
    status_forcelist=[502, 503, 504],
    allowed_methods=frozenset(["POST"]),
    raise_on_status=False,
)
_session = requests.Session()
_session.mount("https://", HTTPAdapter(max_retries=_RETRY))
_session.mount("http://", HTTPAdapter(max_retries=_RETRY))


@retry_on_transient()
def _post_synthesize_request(url, headers, payload, timeout):
    return _session.post(url, json=payload, headers=headers, timeout=timeout)

# CLI 优先，API 作为 fallback
_use_cli = None  # None = 未检测, True = 使用CLI, False = 使用API


def _use_mmx_cli() -> bool:
    """检测是否使用 mmx CLI"""
    global _use_cli
    if _use_cli is not None:
        return _use_cli
    _use_cli = shutil.which("mmx") is not None
    if _use_cli:
        MiniMax.info("[*] 使用 mmx CLI 进行语音合成")
    else:
        MiniMax.warning("[!] mmx CLI 未找到，将使用 API")
    return _use_cli


def synthesize(
    text: str,
    voice_id: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
    speed: float = 1.0,
    vol: float = 1.0,
    pitch: float = 0,
    emotion: str = "calm",
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    output_format: str = DEFAULT_FORMAT,
    language_boost: str = "auto",
) -> bytes:
    """
    合成单段音频，优先CLI，CLI失败自动切换API

    Args:
        language_boost: "auto" 或具体语言（如 "Chinese"、"English"）；
                        mmx CLI 当前未透传此参数，仅在 API fallback 路径生效。

    Returns: 音频字节数据
    """
    # 优先使用 CLI，失败则自动切换到 API
    if _use_mmx_cli():
        try:
            return _synthesize_cli(
                text=text, voice_id=voice_id, speed=speed,
                vol=vol, pitch=pitch, sample_rate=sample_rate,
                output_format=output_format, api_key=api_key
            )
        except AudioProcessingError as e:
            err_msg = str(e)
            if "invalid api key" in err_msg or "login fail" in err_msg or "API error" in err_msg:
                MiniMax.warning(f"[!] mmx CLI 认证失败，切换到 API: {err_msg[:80]}")
            else:
                raise

    # Fallback 到 API
    return _synthesize_api(
        text=text, voice_id=voice_id, api_key=api_key, model=model,
        speed=speed, vol=vol, pitch=pitch, sample_rate=sample_rate,
        output_format=output_format, language_boost=language_boost,
    )


def _synthesize_cli(
    text: str, voice_id: str, speed: float, vol: float, pitch: float,
    sample_rate: int, output_format: str, api_key: str
) -> bytes:
    """使用 mmx CLI 合成"""
    with tempfile.NamedTemporaryFile(suffix=f".{output_format}", delete=False) as f:
        out_path = f.name

    try:
        cmd = [
            "mmx", "speech", "synthesize",
            "--text", text,
            "--voice", voice_id,
            "--speed", str(speed),
            "--volume", str(vol),
            "--pitch", str(pitch),
            "--sample-rate", str(sample_rate),
            "--format", output_format,
            "--out", out_path,
            "--non-interactive",
        ]
        if api_key:
            cmd.extend(["--api-key", api_key])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise AudioProcessingError(f"mmx speech synthesize failed: {result.stderr}")

        with open(out_path, "rb") as f:
            return f.read()
    finally:
        os.unlink(out_path)


def _synthesize_api(
    text: str, voice_id: str, api_key: str, model: str,
    speed: float, vol: float, pitch: float,
    sample_rate: int, output_format: str,
    language_boost: str = "auto",
    subtitle_enable: bool = False,
    subtitle_type: str = "sentence",
) -> Tuple[bytes, dict]:
    """使用 API 合成（fallback）

    Returns: (audio_bytes, response_dict).
             response_dict 包含完整的 API 返回，调用方需要 subtitle_file 时直接取用。
    """
    api_base = CONFIG.get_minimax("api_base", "https://api.minimaxi.com")
    url = f"{api_base}/v1/t2a_v2"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "text": text,
        "stream": False,
        "voice_setting": {
            "voice_id": voice_id,
            "speed": speed,
            "vol": vol,
            "pitch": pitch,
        },
        "audio_setting": {
            "sample_rate": sample_rate,
            "bitrate": 128000,
            "format": output_format,
            "channel": 1,
        },
        "output_format": "hex",
        "language_boost": language_boost,
    }
    if subtitle_enable:
        payload["subtitle_enable"] = True
        payload["subtitle_type"] = subtitle_type

    resp = _post_synthesize_request(url, headers, payload, timeout=60)
    resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError as e:
        raise APIError("MiniMax", f"Invalid JSON response: {e} (status={resp.status_code}, body={resp.text[:200]})")

    base_resp = data.get("base_resp", {})
    trace_id = data.get("trace_id")
    if base_resp.get("status_code") != 0:
        MiniMax.error(f"TTS API error: {base_resp.get('status_msg')} (trace_id={trace_id})")
        raise APIError("MiniMax", base_resp.get("status_msg"), trace_id)

    audio_hex = data.get("data", {}).get("audio")
    if not audio_hex:
        raise RuntimeError("No audio data in response")

    return binascii.unhexlify(audio_hex), data


# ============ 流式合成 (streaming, opt-in) ============
#
# MiniMax T2A v2 在 `stream: true` 时返回 Server-Sent Events:
#   Content-Type: text/event-stream; charset=utf-8
#   Transfer-Encoding: chunked
# 每条事件形如 `data: {<json>}\n\n`, JSON payload:
#   {"data": {"audio": "<hex-mp3>", "status": <1|2>, "ced": ""},
#    "trace_id": "...",
#    "base_resp": {"status_code": 0, "status_msg": "..."},
#    "extra_info": {...}}       // 仅在最后一个事件出现
#
# 字段语义（实测验证, 2026-08）:
#   - 首个事件 data.status = 1, audio 是 MP3 chunk（含 ID3 header）
#   - 中间事件 data.status = 1, audio 是 raw MP3 frame chunk
#   - 最终事件 data.status = 2, audio 是完整音频字节（与前面所有 chunks
#     拼接后大小一致）, extra_info 含 audio_length / audio_size / 比特率等
# 终止条件: 服务端在 status=2 后关闭流（content-length 不存在），无需哨兵。
#
# 公开 API:
#   - synthesize_streaming(...)  -> Iterator[StreamChunk]
#   - collect_stream(...)        -> (bytes, extra_info)
# 同步接口 synthesize() 仍然走非流式 path, 行为不变。

StreamChunk = Tuple[bytes, bool, dict]
#                       ^audio ^final ^event_dict
# final=True 表示该 chunk 所属事件是终止事件（status=2），
# 此时 audio 已是完整音频, 适合直接落盘；中间 chunk 的 audio
# 仅供流式播放（缺 MP3 header）。


def _synthesize_api_stream(
    text: str,
    voice_id: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
    speed: float = 1.0,
    vol: float = 1.0,
    pitch: float = 0,
    emotion: str = "calm",
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    output_format: str = DEFAULT_FORMAT,
    language_boost: str = "auto",
    timeout: int = 60,
) -> Iterator[StreamChunk]:
    """流式合成 MiniMax T2A v2。

    Yields:
        (audio_bytes, is_final, event_dict) 三元组。
        - audio_bytes: 解码自 data.audio 的 hex
        - is_final: True 表示这是终止事件（status=2, 含完整音频）
        - event_dict: 完整 JSON 事件对象，便于 caller 取 extra_info 等

    Raises:
        APIError: API 返回 status_code != 0
        requests.HTTPError: HTTP 状态码非 2xx
    """
    api_base = CONFIG.get_minimax("api_base", "https://api.minimaxi.com")
    url = f"{api_base}/v1/t2a_v2"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "text": text,
        "stream": True,
        "voice_setting": {
            "voice_id": voice_id,
            "speed": speed,
            "vol": vol,
            "pitch": pitch,
        },
        "audio_setting": {
            "sample_rate": sample_rate,
            "bitrate": 128000,
            "format": output_format,
            "channel": 1,
        },
        "output_format": "hex",
        "language_boost": language_boost,
    }
    if emotion:
        payload["voice_setting"]["emotion"] = emotion

    # 流式响应不应走指数退避重试：partial decode 后重发会造成音频重复。
    # 这里用单次请求 + 自定义超时，不复用 _post_synthesize_request。
    resp = requests.post(
        url, json=payload, headers=headers, timeout=timeout, stream=True,
    )
    try:
        resp.raise_for_status()
        # iter_lines 默认返回 bytes; SSE 行是 `data: <json>` 后跟一个空行。
        # 用 decode_unicode=True 让 caller 不用每次手动 decode。
        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            # SSE event line: "data: <json>"; 其它类型（event: / id:）MiniMax
            # 当前未使用，但保留容错: 仅处理以 "data: " 开头的行。
            if not isinstance(raw_line, str):
                raw_line = raw_line.decode("utf-8", errors="replace")
            if not raw_line.startswith("data:"):
                continue
            payload_str = raw_line[len("data:"):].strip()
            if not payload_str:
                continue
            try:
                event = json.loads(payload_str)
            except json.JSONDecodeError:
                MiniMax.warning(f"[!] 流式响应 JSON 解析失败: {payload_str[:120]}")
                continue

            base_resp = event.get("base_resp") or {}
            status_code = base_resp.get("status_code", 0)
            if status_code != 0:
                trace_id = event.get("trace_id")
                msg = base_resp.get("status_msg") or "stream error"
                MiniMax.error(
                    f"TTS stream API error: {msg} (trace_id={trace_id})"
                )
                raise APIError("MiniMax", msg, trace_id)

            data = event.get("data") or {}
            audio_hex = data.get("audio") or ""
            is_final = data.get("status") == 2
            if audio_hex:
                try:
                    audio_bytes = binascii.unhexlify(audio_hex)
                except (binascii.Error, ValueError) as e:
                    MiniMax.warning(f"[!] 音频 hex 解码失败: {e}")
                    continue
                yield (audio_bytes, is_final, event)

            if is_final:
                # 终止事件, 服务端随后关闭流; 提前 return 避免继续等待。
                return
    finally:
        resp.close()


def synthesize_streaming(
    text: str,
    voice_id: str,
    api_key: str,
    **kwargs,
) -> Iterator[StreamChunk]:
    """公开流式入口。参数同 _synthesize_api_stream。

    Usage:
        for audio_bytes, is_final, event in synthesize_streaming(...):
            player.feed(audio_bytes)
            if is_final:
                break
    """
    return _synthesize_api_stream(
        text=text, voice_id=voice_id, api_key=api_key, **kwargs,
    )


def collect_stream(
    text: str,
    voice_id: str,
    api_key: str,
    **kwargs,
) -> Tuple[bytes, dict]:
    """便利函数: 消费整个流, 返回 (完整音频字节, 最终事件 dict)。

    流式响应中, 终止事件（status=2）的 audio 字段已包含完整音频;
    中间 chunks 仅是播放用 raw frames（缺 header, 直接拼接到 mp3 文件
    会得到双倍时长）。因此保存文件时使用终止事件的 audio。
    """
    final_audio: bytes = b""
    final_event: dict = {}
    for audio_bytes, is_final, event in _synthesize_api_stream(
        text=text, voice_id=voice_id, api_key=api_key, **kwargs,
    ):
        if is_final:
            final_audio = audio_bytes
            final_event = event
            break
        # 中间 chunk 仅用于流式播放, 丢弃
    if not final_audio:
        raise RuntimeError("流式响应未收到终止事件 (status=2)")
    return final_audio, final_event


# ============ 字幕相关 ============

def _fetch_subtitle_url(url: str, api_key: str) -> list:
    """通过预签名 OSS URL 拉取字幕 JSON 列表。"""
    # OSS URL 是预签名的，理论上不需要 Authorization 头，但带上以防万一
    resp = requests.get(url, headers={"Authorization": f"Bearer {api_key}"}, timeout=30)
    resp.raise_for_status()
    body = resp.json()
    if not isinstance(body, list):
        raise RuntimeError(f"Unexpected subtitle payload shape: {type(body).__name__}")
    return body


def _normalize_subtitle_items(items: list) -> list:
    """把 MiniMax 字幕条目归一为 [{text, start, end}, ...]（start/end 单位秒）。

    MiniMax 返回 time_begin/time_end（毫秒）；sentence 级别通常整段为一个 item，
    word 级别会带 timestamped_words 子数组。我们按 timestamped_words 拆分（如果存在）
    让 SRT 更细粒度；否则回退到顶层 item。
    """
    out = []
    for item in items:
        text = item.get("text", "").strip()
        if not text:
            continue
        # ms → s
        start_ms = item.get("time_begin", 0)
        end_ms = item.get("time_end", start_ms)

        words = item.get("timestamped_words")
        if words and isinstance(words, list):
            # 按 word 拆分；用所在段落的 text 索引来切分（每 word 的 text 可能没有 raw 字符）
            # 简化做法：每 word 单独一项，用 word 字段本身作为 text
            for w in words:
                wtext = (w.get("word") or w.get("pronounce_word") or "").strip()
                if not wtext:
                    continue
                w_start_ms = w.get("time_begin", start_ms)
                w_end_ms = w.get("time_end", w_start_ms + 1)
                out.append({
                    "text": wtext,
                    "start": float(w_start_ms) / 1000.0,
                    "end": float(w_end_ms) / 1000.0,
                })
        else:
            out.append({
                "text": text,
                "start": float(start_ms) / 1000.0,
                "end": float(end_ms) / 1000.0,
            })
    return out


def _subtitle_json_to_srt(items: list) -> str:
    """把 [{text, start, end}, ...] 转 SRT 文本。

    Args:
        items: 字幕条目列表（start/end 单位：秒）。
    """
    def fmt(t: float) -> str:
        # 防负数；超过 24h 的极端情况也兜底
        t = max(0.0, t)
        total_ms = int(round(t * 1000))
        h, rem = divmod(total_ms, 3600 * 1000)
        m, rem = divmod(rem, 60 * 1000)
        s, ms = divmod(rem, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines = []
    counter = 0
    for item in items:
        start = item.get("start", 0.0)
        end = item.get("end", start + 1.0)
        text = (item.get("text") or "").strip()
        if not text:
            continue
        # 防 end <= start（SRT 播放器可能拒绝）
        if end <= start:
            end = start + 0.5
        counter += 1
        lines.append(str(counter))
        lines.append(f"{fmt(start)} --> {fmt(end)}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines)


def synthesize_with_subtitles(
    text: str,
    voice_id: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
    speed: float = 1.0,
    vol: float = 1.0,
    pitch: float = 0,
    emotion: str = "calm",
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    output_format: str = "mp3",
    language_boost: str = "auto",
    subtitle_type: str = "sentence",
    timeout: int = 60,
) -> Tuple[bytes, str, list]:
    """合成音频并返回字幕（SRT 文本 + 原始 JSON 列表）。

    字幕工作流程（实测验证）：
      1. POST /v1/t2a_v2 带 subtitle_enable=true、subtitle_type=sentence|word
      2. 响应 data.subtitle_file 是一个 OSS 预签名 URL（不是 file_id！）
      3. GET 该 URL 获得字幕 JSON 列表
      4. 把列表归一化为 [{text, start, end}]（start/end 单位：秒）
      5. 转 SRT 文本

    Returns:
        (audio_bytes, srt_text, normalized_subtitle_items)
        若字幕获取失败，会记录警告并返回 ("", [])；audio_bytes 仍然有效。
    """
    # 字幕需要 subtitle_file 字段，mmx CLI 不支持；强制走 API 路径
    audio_bytes, response = _synthesize_api(
        text=text, voice_id=voice_id, api_key=api_key, model=model,
        speed=speed, vol=vol, pitch=pitch,
        sample_rate=sample_rate, output_format=output_format,
        language_boost=language_boost,
        subtitle_enable=True, subtitle_type=subtitle_type,
    )

    subtitle_url = response.get("data", {}).get("subtitle_file")
    if not subtitle_url:
        MiniMax.warning("[!] API 响应中未找到 subtitle_file 字段")
        return audio_bytes, "", []

    try:
        raw_items = _fetch_subtitle_url(subtitle_url, api_key)
        items = _normalize_subtitle_items(raw_items)
        srt = _subtitle_json_to_srt(items)
        return audio_bytes, srt, items
    except Exception as e:
        MiniMax.warning(f"[!] 字幕获取失败: {e}")
        return audio_bytes, "", []


def synthesize_segments(
    segments: List[dict],
    voice1_id: str,
    voice2_id: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
    speaker_map: dict = None,
    speed_map: dict = None,
    progress_callback=None,
    output_format: str = DEFAULT_FORMAT,
    language_boost: str = "auto",
    on_log=None,
) -> Tuple[List[str], str]:
    """
    按段落合成音频并合并

    Args:
        segments: [{"speaker": "speaker_name", "text": "..."}, ...]
                  speaker 名称会映射到 voice1 或 voice2
        voice1_id: 默认第一个音色 ID（speaker_map 未命中时回退）
        voice2_id: 默认第二个音色 ID（speaker_map 未命中时回退）
        api_key: MiniMax API key
        model: 模型名称
        speaker_map: 显式 speaker 标签到 voice_id 的映射（优先于首现顺序）
                    例如 {"主播甲": "male-qn-qingse", "主播乙": "female-shaonv"}
        speed_map: speaker 标签到 speed 的映射（可选），例如 {"主播甲": 1.2, "主播乙": 0.9}
                   未命中时使用默认 1.0
        output_format: 音频格式（mp3 / pcm / flac / wav / opus 等）
        language_boost: "auto" / "Chinese" / "English" 等
        on_log: 段级日志回调 callable(str)；让 GUI 看到"合成段 N/M..."，
                避免 1 段脚本时进度条卡 0% 让人误以为程序死了。

    Returns:
        (merged_output_path, combined_order) - 合并后的文件路径 和 段落顺序描述
    """
    def _log(msg: str):
        if on_log:
            on_log(msg)
    os.makedirs("output", exist_ok=True)

    if speaker_map:
        # 优先：调用方提供的显式映射（推荐，避免首现顺序导致性别错乱）
        speaker_to_voice = dict(speaker_map)
    else:
        # 回退：按首次出现顺序
        speakers_order = []
        for seg in segments:
            s = seg.get("speaker", "")
            if s not in speakers_order:
                speakers_order.append(s)

        # 映射 speaker -> voice_id
        speaker_to_voice = {}
        if len(speakers_order) >= 1:
            speaker_to_voice[speakers_order[0]] = voice1_id
        if len(speakers_order) >= 2:
            speaker_to_voice[speakers_order[1]] = voice2_id
        # 如果超过2个，回退到 voice1
        for s in speakers_order[2:]:
            speaker_to_voice[s] = voice1_id

    # 把本地 voice_id 解析到 MiniMax 服务端 voice_id（如果音色库有记录）
    # 用户在 GUI 里 voice_map 填的是本地 voice_id（如 "talarico-james"），但 MiniMax
    # /v1/t2a_v2 需要服务端 ID（如 "talarico-clone-001"）。
    # 自动转换：若本地 record 带 minimax_voice_id 字段，则用之；否则原样传。
    # 走 voice_resolver：看板录的音色只在 SQLite 的 voice_clones 表里，
    # 只查遗留 JSON 会拿不到映射（2026-10-21 修）。
    try:
        import voice_resolver as _vr
        _resolved = {}
        for _local_vid in set(speaker_to_voice.values()):
            if not _local_vid:
                continue
            _server_vid = _vr.resolve_minimax_voice_id(_local_vid)
            _resolved[_local_vid] = _server_vid if _server_vid else _local_vid
        for _sp in list(speaker_to_voice.keys()):
            _orig = speaker_to_voice[_sp]
            if _orig in _resolved:
                speaker_to_voice[_sp] = _resolved[_orig]
    except Exception as _e:
        pass  # 音色库解析失败 → 用原始 ID（保持向后兼容）

    # 按段落合成
    segment_files = []
    segment_order = []  # 记录段落顺序用于后续合并

    ext = output_format if output_format in ("mp3", "wav", "flac", "opus") else "mp3"

    _total = sum(1 for s in segments if s.get("text", "").strip())
    _log(f"MiniMax ({model}) 合成开始：共 {_total} 段")

    for i, seg in enumerate(segments):
        speaker = seg.get("speaker", "")
        text = seg.get("text", "").strip()
        if not text:
            continue

        voice_id = speaker_to_voice.get(speaker, voice1_id or "")
        # 用角色标签（"主播甲"）作为文件名前缀，便于排查；找不到则用通用 vN
        voice_label = next(
            (label for label, v in speaker_to_voice.items() if v == voice_id),
            f"v{i}",
        )

        # 查找该 speaker 的语速，未命中回退 1.0
        seg_speed = (speed_map or {}).get(speaker, 1.0)

        # 段级日志：让用户在 1 段脚本时也能看到"动起来"
        preview = text[:30] + ("…" if len(text) > 30 else "")
        _log(f"合成段 {i+1}/{_total} [{speaker} → {voice_id[:20]}] speed={seg_speed:.2f}x : {preview}")

        fname = f"output/minimax_{voice_label}_{i}.{ext}"
        audio_bytes = synthesize(
            text=text,
            voice_id=voice_id,
            api_key=api_key,
            model=model,
            speed=seg_speed,
            output_format=output_format,
            language_boost=language_boost,
        )
        with open(fname, "wb") as f:
            f.write(audio_bytes)
        segment_files.append(fname)
        segment_order.append((fname, speaker, voice_label))
        if progress_callback is not None:
            try:
                progress_callback(len(segment_files), sum(1 for s in segments if s.get("text", "").strip()))
            except Exception:
                pass  # don't let callback errors break synthesis

    if not segment_files:
        raise ValueError("No segments to synthesize")

    # 合并所有段落（mp3 走 libmp3lame，其他格式走 copy 即可）
    combined = "output/podcast_minimax_combined.mp3"
    _concat_segments(combined, segment_files, output_format=ext)

    # 清理临时分段落文件
    for f in segment_files:
        os.unlink(f)

    # Master loudnorm（2026-10-11 新增；minimax 之前根本没有任何归一化）
    # -16 LUFS / -1.0 dBTP / LRA 11 是 Apple Podcasts 规范；
    # loudnorm 仅安全用于 mp3，其他格式（wav/flac/opus）跳过避免破坏 raw 数据。
    if ext == "mp3":
        try:
            import time as _time, tempfile as _tempfile, shutil as _shutil
            _t0 = _time.time()
            _log(f"开始 master loudnorm（minimax {len(segment_files)} 段）…")
            with _tempfile.NamedTemporaryFile(
                prefix="minimax_master_", suffix=".mp3",
                delete=False, dir="output",
            ) as _tf:
                _concat_tmp = _tf.name
            _shutil.copy2(combined, _concat_tmp)
            stats = _master_loudnorm_mp3(_concat_tmp, combined)
            os.unlink(_concat_tmp)
            dt = _time.time() - _t0
            in_lufs = stats.get("input_lufs")
            out_lufs = stats.get("output_lufs", stats.get("target_lufs"))
            _log(
                f"✅ master loudnorm 完成 {dt:.1f}s: "
                f"input {in_lufs:+.1f} LUFS → output {out_lufs:+.1f} LUFS "
                f"(target -16.0)"
            )
        except Exception as e:
            _log(f"⚠ master loudnorm 失败，用 raw concat 输出: {e}")
            # 不阻塞主流程

    order_desc = " -> ".join([f"{s}({v})" for _, s, v in segment_order])
    return combined, order_desc


def _concat_mp3(output: str, files: List[str]):
    """用 ffmpeg 合并多个 MP3 文件"""
    _concat_segments(output, files, output_format="mp3")


def _concat_segments(output: str, files: List[str], output_format: str = "mp3"):
    """用 ffmpeg concat 协议合并多个同格式音频文件。

    mp3 走 libmp3lame 重编码（保证 concat 不爆音）；其他格式走 copy 即可。
    """
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        list_file = f.name
        for fp in files:
            f.write(f"file '{os.path.abspath(fp)}'\n")

    cmd = ['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', list_file]
    if output_format == "mp3":
        cmd += ['-acodec', 'libmp3lame', output]
    else:
        # 其他格式（wav/flac/opus）使用 copy 保持原编码
        cmd += ['-c', 'copy', output]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    os.unlink(list_file)
    if result.returncode != 0:
        raise AudioProcessingError(f"ffmpeg concat failed: {result.stderr[:200]}")


def parse_script(script_text: str) -> List[dict]:
    """
    解析台词文本，返回段落列表

    格式：【speaker】\ntext\n\n---\n\n...
    """
    segments = []
    blocks = script_text.split("\n\n---\n\n")
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        # 找到第一个】的位置
        brace_end = block.find("】")
        if brace_end == -1:
            continue
        speaker = block[1:brace_end]
        content = block[brace_end + 1:].strip()
        if speaker and content:
            segments.append({"speaker": speaker, "text": content})
    return segments
