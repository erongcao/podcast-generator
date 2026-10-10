#!/usr/bin/env python3
"""
MiniMax 异步长文本 TTS 客户端。

适用场景：单段文本 > 10K 字符（同步 /v1/t2a_v2 上限）。
异步端点能力：
  - POST /v1/t2a_async_v2 接收 text（≤50K chars）或 text_file_id（≤1M chars）。
  - 提交后返回 task_id / file_id / task_token。
  - 输出是一个 tar 包（包含 *.mp3、*.titles 字幕 JSON、*.extra 元信息 JSON）。

实测验证的轮询契约（2026-08 通过 curl 探测得出）：
  - **没有专门的 status 查询端点**。/v1/t2a_async_query、/v1/t2a_async_status、
    /v1/t2a_async_retrieve 等都返回 404。
  - 进度查询应使用 GET /v1/files/retrieve?file_id=<id>：
      * 处理中：{"file": null, "base_resp": {"status_code": 2013, "status_msg": "file not found"}}
      * 完成：  {"file": {file_id, bytes, download_url, ...}, "base_resp": {"status_code": 0}}
    判定标准：data.get("file") is not None 且 data.get("file", {}).get("download_url") 存在。
  - 文件下载推荐使用 file.download_url（OSS 预签名 URL，无 auth 头也能 GET）；
    也可走 GET /v1/files/retrieve_content?file_id=<id>（返回 tar 字节）。

file_id 与 task_id 实际上是同一个数字 id（提交响应里两者相等）。
"""

import io
import json
import tarfile
import time
from typing import Optional

import requests

from exceptions import APIError
from logger import MiniMax
from network_retry import retry_on_transient

ASYNC_SUBMIT_URL = "https://api.minimaxi.com/v1/t2a_async_v2"
FILE_UPLOAD_URL = "https://api.minimaxi.com/v1/files/upload"
FILE_RETRIEVE_URL = "https://api.minimaxi.com/v1/files/retrieve"
FILE_RETRIEVE_CONTENT_URL = "https://api.minimaxi.com/v1/files/retrieve_content"

# text 字段直传上限；超过此值走文件上传路径
TEXT_DIRECT_LIMIT = 50_000

# 处理中文件的特征响应（status_code 非 0 即未就绪）
_PENDING_BASE_RESP_CODES = {2013}  # "file not found"


# ============ 网络层（带重试） ============

@retry_on_transient()
def _post_submit(payload, headers):
    return requests.post(ASYNC_SUBMIT_URL, json=payload, headers=headers, timeout=60)


@retry_on_transient()
def _post_upload(content_bytes, filename, headers):
    return requests.post(
        FILE_UPLOAD_URL,
        headers=headers,
        files={"file": (filename, io.BytesIO(content_bytes), "text/plain")},
        data={"purpose": "t2a_async_input"},
        timeout=60,
    )


@retry_on_transient()
def _get_file_meta(file_id, headers):
    return requests.get(
        f"{FILE_RETRIEVE_URL}?file_id={file_id}",
        headers=headers,
        timeout=30,
    )


@retry_on_transient()
def _get_file_bytes(file_id, headers):
    return requests.get(
        f"{FILE_RETRIEVE_CONTENT_URL}?file_id={file_id}",
        headers=headers,
        timeout=300,
    )


@retry_on_transient()
def _download_url(url):
    """直接从 OSS 预签名 URL 拉取。无需 Authorization 头。"""
    return requests.get(url, timeout=300)


# ============ 提交 ============

def _build_payload(
    text_or_file_id: str,
    is_file_id: bool,
    voice_id: str,
    model: str,
    speed: float,
    vol: float,
    pitch: int,
    emotion: Optional[str],
    audio_setting: dict,
    language_boost: Optional[str],
    subtitle_enable: bool,
    subtitle_type: str,
) -> dict:
    """构造 /v1/t2a_async_v2 的请求体。"""
    payload = {
        "model": model,
        "voice_setting": {
            "voice_id": voice_id,
            "speed": speed,
            "vol": vol,
            "pitch": pitch,
        },
        "audio_setting": audio_setting,
    }
    if emotion:
        payload["voice_setting"]["emotion"] = emotion
    if language_boost:
        payload["language_boost"] = language_boost
    if is_file_id:
        payload["text_file_id"] = text_or_file_id
    else:
        payload["text"] = text_or_file_id
    if subtitle_enable:
        payload["subtitle_enable"] = True
        payload["subtitle_type"] = subtitle_type
    return payload


def submit_long_text(
    text: str,
    voice_id: str,
    api_key: str,
    model: str = "speech-2.8-hd",
    speed: float = 1.0,
    vol: float = 1.0,
    pitch: int = 0,
    emotion: str = "calm",
    audio_format: str = "mp3",
    sample_rate: int = 32000,
    bitrate: int = 128000,
    channel: int = 1,
    language_boost: Optional[str] = None,
    subtitle_enable: bool = False,
    subtitle_type: str = "sentence",
) -> dict:
    """提交异步 TTS 任务。

    长文本（>50K）自动走文件上传路径（先 POST /v1/files/upload，
    purpose=t2a_async_input，再在 submit payload 里用 text_file_id 引用）。

    Args:
        text: 待合成文本。
        voice_id: MiniMax voice_id。
        api_key: MiniMax API key。
        其余参数含义与同步 /v1/t2a_v2 一致。

    Returns:
        dict 形如 {"task_id": ..., "file_id": ..., "task_token": ...}。

    Raises:
        APIError: 提交被 API 拒绝（base_resp.status_code != 0）。
    """
    headers = {"Authorization": f"Bearer {api_key}"}

    # 长文本路径：先上传文本文件
    if len(text) > TEXT_DIRECT_LIMIT:
        MiniMax.info(
            f"[*] 文本长度 {len(text)} > {TEXT_DIRECT_LIMIT}，走文件上传路径..."
        )
        upload_resp = _post_upload(text.encode("utf-8"), "t2a_input.txt", headers)
        upload_resp.raise_for_status()
        try:
            upload_data = upload_resp.json()
        except ValueError as e:
            raise APIError(
                "MiniMax",
                f"text file upload returned non-JSON: {e} "
                f"(status={upload_resp.status_code}, body={upload_resp.text[:200]})",
            )
        file_id = upload_data.get("file", {}).get("file_id")
        if not file_id:
            raise APIError("MiniMax", f"text file upload failed: {upload_data}")
        is_file_id = True
        text_arg = file_id
    else:
        is_file_id = False
        text_arg = text

    payload = _build_payload(
        text_or_file_id=text_arg,
        is_file_id=is_file_id,
        voice_id=voice_id,
        model=model,
        speed=speed, vol=vol, pitch=pitch, emotion=emotion,
        audio_setting={
            "format": audio_format,
            "sample_rate": sample_rate,
            "bitrate": bitrate,
            "channel": channel,
        },
        language_boost=language_boost,
        subtitle_enable=subtitle_enable,
        subtitle_type=subtitle_type,
    )

    resp = _post_submit(payload, headers)
    resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError as e:
        raise APIError(
            "MiniMax",
            f"async submit returned non-JSON: {e} "
            f"(status={resp.status_code}, body={resp.text[:200]})",
        )

    base = data.get("base_resp", {})
    if base.get("status_code", -1) != 0:
        raise APIError(
            "MiniMax",
            base.get("status_msg", "unknown"),
            trace_id=data.get("trace_id"),
        )

    return {
        "task_id": data.get("task_id"),
        "file_id": data.get("file_id"),
        "task_token": data.get("task_token"),
    }


# ============ 轮询 ============

def _is_task_ready(meta_resp_json: dict) -> bool:
    """判定文件元信息响应是否表明任务完成。

    处理中：{"file": null, "base_resp": {"status_code": 2013, ...}}
    完成：  {"file": {file_id, bytes, download_url, ...}, ...}
    """
    file_meta = meta_resp_json.get("file")
    if not file_meta:
        return False
    # download_url 是关键——处理中不会有
    return bool(file_meta.get("download_url"))


def poll_for_completion(
    task_id,
    api_key: str,
    file_id=None,
    timeout: float = 600.0,
    poll_interval: float = 3.0,
) -> dict:
    """轮询直到异步任务完成或超时。

    实际是对 GET /v1/files/retrieve?file_id=... 的轮询——
    处理中返回 base_resp.status_code=2013（"file not found"），
    完成后返回 file.download_url 等元信息。

    Args:
        task_id: submit 返回的 task_id（仅用于日志）。
        api_key: API key。
        file_id: submit 返回的 file_id。MiniMax 实际返回里 file_id==task_id，
                 但显式传入更清晰；若未传则用 task_id。
        timeout: 最长等待秒数。
        poll_interval: 两次轮询之间的间隔秒数。

    Returns:
        file 元信息 dict（含 download_url）。

    Raises:
        TimeoutError: 超过 timeout 仍未完成。
        APIError: 其他非预期响应。
    """
    headers = {"Authorization": f"Bearer {api_key}"}
    fid = file_id or task_id
    deadline = time.time() + timeout
    attempt = 0

    while time.time() < deadline:
        attempt += 1
        resp = _get_file_meta(fid, headers)
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError as e:
            raise APIError(
                "MiniMax",
                f"file retrieve returned non-JSON: {e} "
                f"(status={resp.status_code}, body={resp.text[:200]})",
            )

        base = data.get("base_resp", {})
        code = base.get("status_code")

        if code == 0 and _is_task_ready(data):
            MiniMax.info(f"[*] 异步任务 {task_id} 完成（轮询 {attempt} 次）")
            return data["file"]

        if code in _PENDING_BASE_RESP_CODES:
            MiniMax.info(f"[*] 异步任务 {task_id} 处理中（尝试 #{attempt}）")
        elif code != 0:
            # 其他错误码 = 真错误
            raise APIError(
                "MiniMax",
                base.get("status_msg", f"status_code={code}"),
            )

        time.sleep(poll_interval)

    raise TimeoutError(
        f"async task {task_id} (file_id={fid}) did not complete within {timeout}s"
    )


# ============ 下载 & 解包 ============

def download_audio(file_id: str, api_key: str) -> bytes:
    """从 file_id 下载合成结果（tar 字节流）。

    Returns:
        tar 归档的原始字节（包含 .mp3 / .titles / .extra）。
    """
    headers = {"Authorization": f"Bearer {api_key}"}
    resp = _get_file_bytes(file_id, headers)
    resp.raise_for_status()
    return resp.content


def download_audio_from_url(download_url: str) -> bytes:
    """从 OSS 预签名 URL 直接下载（无需 API key）。"""
    resp = _download_url(download_url)
    resp.raise_for_status()
    return resp.content


def extract_tar(tar_bytes: bytes) -> dict:
    """解包异步结果 tar，返回 {"mp3": bytes, "titles": list, "extra": dict}。

    tar 内容通常形如：
        content-<hash>.mp3
        content-<hash>.titles   (JSON list of {text, time_begin, time_end, ...})
        content-<hash>.extra    (JSON {audio_length, audio_sample_rate, ...})

    如果字段缺失，对应 key 返回 None（不抛错）。
    """
    out = {"mp3": None, "titles": None, "extra": None}
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            f = tar.extractfile(member)
            if f is None:
                continue
            name = member.name.lower()
            content = f.read()
            if name.endswith(".mp3"):
                out["mp3"] = content
            elif name.endswith(".titles"):
                try:
                    out["titles"] = json.loads(content)
                except (ValueError, UnicodeDecodeError):
                    MiniMax.warning(f"[!] titles 文件解析失败: {member.name}")
            elif name.endswith(".extra"):
                try:
                    out["extra"] = json.loads(content)
                except (ValueError, UnicodeDecodeError):
                    MiniMax.warning(f"[!] extra 文件解析失败: {member.name}")
    return out


# ============ 高层封装 ============

def synthesize_long_text(
    text: str,
    voice_id: str,
    api_key: str,
    model: str = "speech-2.8-hd",
    speed: float = 1.0,
    vol: float = 1.0,
    pitch: int = 0,
    emotion: str = "calm",
    audio_format: str = "mp3",
    sample_rate: int = 32000,
    bitrate: int = 128000,
    channel: int = 1,
    language_boost: Optional[str] = None,
    subtitle_enable: bool = False,
    subtitle_type: str = "sentence",
    poll_timeout: float = 600.0,
    poll_interval: float = 3.0,
) -> dict:
    """高层封装：submit + poll + download，返回解包后的 {"mp3", "titles", "extra"}。

    Returns:
        dict: {"mp3": bytes, "titles": list | None, "extra": dict | None}

    Raises:
        APIError, TimeoutError, requests.HTTPError 等。
    """
    submit = submit_long_text(
        text=text,
        voice_id=voice_id,
        api_key=api_key,
        model=model,
        speed=speed, vol=vol, pitch=pitch, emotion=emotion,
        audio_format=audio_format, sample_rate=sample_rate,
        bitrate=bitrate, channel=channel,
        language_boost=language_boost,
        subtitle_enable=subtitle_enable, subtitle_type=subtitle_type,
    )
    MiniMax.info(f"[*] 提交异步任务: task_id={submit['task_id']}")
    file_meta = poll_for_completion(
        task_id=submit["task_id"],
        api_key=api_key,
        file_id=submit["file_id"],
        timeout=poll_timeout,
        poll_interval=poll_interval,
    )
    # 优先走 OSS 直接下载（更快，免 Authorization 头）
    download_url = file_meta.get("download_url")
    if download_url:
        tar_bytes = download_audio_from_url(download_url)
    else:
        tar_bytes = download_audio(submit["file_id"], api_key)
    return extract_tar(tar_bytes)