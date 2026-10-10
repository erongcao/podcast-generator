#!/usr/bin/env python3
"""
豆包播客大模型 - 主逻辑：generate_podcast()

This is the core entry point. Connects to the Volcengine PodcastTTS
WebSocket API, drives the connection / session lifecycle, collects
audio chunks, and saves the merged result via ffmpeg (either by
downloading the server-provided audio_url or by concatenating the
per-round mp3 chunks).
"""

import asyncio
import json
import os
import subprocess
import uuid
from pathlib import Path

import websockets

from config import CONFIG
from exceptions import APIError
from logger import PodcastTTS

from doubao_protocol import (
    Message,
    MsgType,
    EventType,
)
from doubao_ws import (
    send_start_connection,
    wait_for_event,
    send_start_session,
    send_finish_session,
    send_finish_connection,
)


# ============== 配置 ==============
DEFAULT_APP_ID = ""
DEFAULT_ACCESS_TOKEN = ""

SPEAKERS = [
    "zh_male_dayixiansheng_v2_saturn_bigtts",
    "zh_female_mizaitongxue_v2_saturn_bigtts",
]
AUDIO_FORMAT = "mp3"
SAMPLE_RATE = 24000


# ============== 核心逻辑 ==============

async def generate_podcast(
    file_url: str = None,
    text: str = None,
    output_path: str = "output.mp3",
    appid: str = None,
    token: str = None,
    speakers: list = None,
    only_nlp_text: bool = False,
    nlp_texts: list = None,
):
    """
    生成播客音频

    Args:
        file_url: 公网可访问的文件/网页 URL
        text: 直接传入的文本内容（action=0 模式）
        output_path: 输出文件路径
        appid: 火山引擎 APP ID
        token: Access Token
        speakers: 发音人列表
        only_nlp_text: True=只生成台词文本（预览模式），不生成音频
        nlp_texts: 结构化台词列表（action=3 模式），格式：[{"speaker": "xxx", "text": "xxx"}]
    """
    appid = appid or CONFIG.get_volcengine("app_id", "")
    token = token or CONFIG.get_volcengine("access_token", "")
    resource_id = CONFIG.get_volcengine("resource_id", "volc.service_type.10050")
    app_key = CONFIG.get_volcengine("app_key", "")
    wss_url = CONFIG.get_volcengine("wss_url", "wss://openspeech.bytedance.com/api/v3/sami/podcasttts")
    speakers = speakers or SPEAKERS

    headers = {
        "X-Api-App-Id": appid,
        "X-Api-Access-Key": token,
        "X-Api-Resource-Id": resource_id,
        "X-Api-App-Key": app_key,
    }

    session_id = str(uuid.uuid4())
    PodcastTTS.info(f"[*] Session: {session_id}")
    PodcastTTS.info(f"[*] 输入: {file_url or (text[:50] if text else str(nlp_texts[:2]) if nlp_texts else None)}")

    # 构建请求参数
    req_params = {
        "input_id": f"podcast_{session_id[:8]}",
        "action": 3 if nlp_texts else 0,  # 3=使用nlp_texts直接生成，0=长文本总结模式
        "use_head_music": False,
        "audio_config": {
            "format": AUDIO_FORMAT,
            "sample_rate": SAMPLE_RATE,
            "speech_rate": 0,
        },
        "speaker_info": {
            "random_order": True,
            "speakers": speakers,
        },
        "input_info": {
            "return_audio_url": not only_nlp_text,
            "only_nlp_text": only_nlp_text,
        },
        "aigc_watermark": False,
    }

    if nlp_texts:
        # action=3: 直接使用结构化台词
        req_params["nlp_texts"] = nlp_texts
    elif file_url:
        req_params["input_info"]["input_url"] = file_url
    elif text:
        req_params["input_text"] = text
    else:
        raise ValueError("必须提供 file_url 或 text")

    payload_bytes = json.dumps(req_params, ensure_ascii=False).encode("utf-8")

    # 收集音频
    audio_chunks = []  # 保留但不再用于最终合并
    round_files = []    # 记录每轮分片文件路径
    podcast_texts = []
    audio_url = None
    os.makedirs("output", exist_ok=True)

    async with websockets.connect(wss_url, additional_headers=headers,
                                    close_timeout=60, ping_timeout=120) as ws:
        # Step 1: StartConnection
        await send_start_connection(ws)
        await wait_for_event(ws, EventType.ConnectionStarted)

        # Step 2: StartSession
        await send_start_session(ws, session_id, payload_bytes)
        await wait_for_event(ws, EventType.SessionStarted)

        # Step 3: 接收播客流
        current_voice = None
        current_audio = bytearray()
        round_id = -999

        while True:
            msg = await ws.recv()
            if not isinstance(msg, bytes):
                continue
            resp = Message.from_bytes(msg)

            # 音频数据帧
            if resp.type == MsgType.AudioOnlyServer and resp.event == EventType.PodcastRoundResponse:
                if resp.payload and not only_nlp_text:
                    audio_chunks.append(resp.payload)
                    current_audio.extend(resp.payload)

            # Round 开始
            elif resp.type == MsgType.FullServerResponse and resp.event == EventType.PodcastRoundStart:
                data = json.loads(resp.payload.decode("utf-8"))
                current_voice = data.get("speaker", "unknown")
                round_id = data.get("round_id", -1)
                if round_id == -1:
                    current_voice = "head_music"
                elif round_id == 9999:
                    current_voice = "tail_music"
                current_audio = bytearray()
                PodcastTTS.info(f"[~] Round {round_id} 开始: {current_voice}")
                # 从 RoundStart 收集文本（only_nlp_text 模式）
                if data.get("text") and data.get("text") not in ["", "None"]:
                    podcast_texts.append({
                        "speaker": current_voice,
                        "text": data.get("text"),
                        "round_id": round_id
                    })

            # Round 结束
            elif resp.type == MsgType.FullServerResponse and resp.event == EventType.PodcastRoundEnd:
                data = json.loads(resp.payload.decode("utf-8"))
                PodcastTTS.info(f"[+] Round {round_id} 结束: {data}")
                if current_audio and not only_nlp_text:
                    fname = f"output/{current_voice}_{round_id}.{AUDIO_FORMAT}"
                    with open(fname, "wb") as f:
                        f.write(current_audio)
                    round_files.append(fname)
                    PodcastTTS.info(f"    保存: {fname}")
                if data.get("text"):
                    podcast_texts.append({
                        "speaker": current_voice,
                        "text": data.get("text"),
                        "round_id": round_id
                    })

            # 播客结束
            elif resp.type == MsgType.FullServerResponse and resp.event == EventType.PodcastEnd:
                data = json.loads(resp.payload.decode("utf-8"))
                PodcastTTS.info(f"[+] 播客结束: {data}")
                meta = data.get("meta_info", {})
                audio_url = meta.get("audio_url")
                topics = meta.get("topics")
                metrics = meta.get("input_metrics", {})
                if audio_url:
                    PodcastTTS.info(f"[+] 音频下载URL: {audio_url}")
                if topics:
                    PodcastTTS.info(f"[*] 生成主题: {topics}")
                if metrics:
                    PodcastTTS.info(f"[*] 文本: 原始{metrics.get('origin_input_text_length')}字符, "
                                 f"处理{metrics.get('input_text_length')}字符, "
                                 f"截断={metrics.get('input_text_truncated')}")
                break

            # Session 结束
            elif resp.type == MsgType.FullServerResponse and resp.event == EventType.SessionFinished:
                PodcastTTS.info("[+] SessionFinished")
                break

            # 错误
            elif resp.type == MsgType.Error:
                err_msg = resp.payload.decode("utf-8") if resp.payload else "(空错误)"
                PodcastTTS.error(f"Server error: {err_msg}")
                raise APIError("Volcengine", err_msg)

        # Step 4: FinishSession
        await send_finish_session(ws, session_id)
        await wait_for_event(ws, EventType.SessionFinished)

        # Step 5: FinishConnection
        await send_finish_connection(ws)

    # 保存合并音频：优先从服务器下载完整音频
    out = Path(output_path).expanduser()

    # 预览模式：只返回台词，不生成音频
    if only_nlp_text:
        PodcastTTS.info(f"[*] 预览模式：只生成台词，共 {len(podcast_texts)} 轮")
        return {
            "audio_url": None,
            "output_path": None,
            "texts": podcast_texts,
        }

    if audio_url:
        # 优先：直接从服务器下载完整音频
        try:
            result = subprocess.run([
                'ffmpeg', '-y', '-i', audio_url,
                '-acodec', 'libmp3lame',
                str(out)
            ], capture_output=True, text=True, timeout=120)
            if result.returncode == 0:
                PodcastTTS.info(f"[+] 完整音频已下载: {out}")
            else:
                PodcastTTS.warning(f"[-] 音频下载失败: {result.stderr[:200]}")
                raise subprocess.CalledProcessError(result.returncode, 'ffmpeg')
        except (subprocess.CalledProcessError, Exception) as e:
            PodcastTTS.warning(f"[-] 下载失败，尝试合并分片: {e}")
            audio_url = None  # 标记下载失败，使用分片合并

    if not audio_url and round_files:
        # 备用：用 ffmpeg 合并所有分片 MP3 文件
        list_file = out.with_suffix('.txt')
        with open(list_file, 'w') as f:
            for rf in round_files:
                f.write(f"file '{os.path.basename(rf)}'\n")
        try:
            result = subprocess.run([
                'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
                '-i', str(list_file),
                '-acodec', 'libmp3lame',
                str(out)
            ], capture_output=True, text=True, timeout=60)
            if result.returncode == 0:
                PodcastTTS.info(f"[+] 完整音频已保存（ffmpeg合并）: {out}")
            else:
                PodcastTTS.warning(f"[-] ffmpeg合并失败: {result.stderr[:200]}")
                # 回退：直接合并
                with open(out, "wb") as fout:
                    for rf in round_files:
                        with open(rf, "rb") as fr:
                            fout.write(fr.read())
                PodcastTTS.info(f"[+] 完整音频已保存（直接合并）: {out}")
        finally:
            list_file.unlink(missing_ok=True)
    elif not audio_url and audio_chunks:
        # 兜底：使用原始分片
        with open(out, "wb") as fout:
            for chunk in audio_chunks:
                fout.write(chunk)
        PodcastTTS.info(f"[+] 完整音频已保存: {out}")

    # 打印播客文本
    if podcast_texts:
        PodcastTTS.info("\n" + "=" * 50)
        PodcastTTS.info("生成播客文本:")
        PodcastTTS.info("=" * 50)
        for item in podcast_texts:
            speaker = item["speaker"]
            text = item["text"]
            if speaker not in ["head_music", "tail_music"]:
                PodcastTTS.info(f"\n[{speaker}]: {text}")

    return {
        "audio_url": audio_url,
        "output_path": str(Path(output_path).resolve()) if (audio_url or round_files or audio_chunks) else None,
        "texts": podcast_texts,
    }