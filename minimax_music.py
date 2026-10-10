#!/usr/bin/env python3
"""
MiniMax 音乐生成模块
用于生成播客片头片尾 BGM

优先使用 mmx CLI，fallback 到 API
"""

import binascii
import os
import shutil
import subprocess
import tempfile

import requests

from config import CONFIG
from exceptions import APIError
from logger import MiniMax
from network_retry import retry_on_transient

DEFAULT_MODEL = "music-2.5+"

# CLI 优先，API 作为 fallback
_use_cli = None  # None = 未检测, True = 使用CLI, False = 使用API


def _use_mmx_cli() -> bool:
    """检测是否使用 mmx CLI"""
    global _use_cli
    if _use_cli is not None:
        return _use_cli
    _use_cli = shutil.which("mmx") is not None
    if _use_cli:
        MiniMax.info("[*] 使用 mmx CLI 进行音乐生成")
    else:
        MiniMax.warning("[!] mmx CLI 未找到，将使用 API")
    return _use_cli


@retry_on_transient()
def _post_bgm_request(url, headers, payload, timeout):
    return requests.post(url, json=payload, headers=headers, timeout=timeout)


def generate_bgm(
    prompt: str,
    api_key: str,
    is_instrumental: bool = True,
    model: str = DEFAULT_MODEL,
) -> bytes:
    """
    生成纯音乐 BGM

    Args:
        prompt: 音乐风格描述（如 "calm ambient piano"）
        api_key: MiniMax API key
        is_instrumental: True=纯音乐，False=带歌词
        model: 模型名称

    Returns: 音频字节数据
    """
    # 优先使用 CLI
    if _use_mmx_cli():
        return _generate_bgm_cli(prompt=prompt, is_instrumental=is_instrumental, api_key=api_key)
    # Fallback 到 API
    return _generate_bgm_api(prompt=prompt, api_key=api_key, is_instrumental=is_instrumental, model=model)


def _generate_bgm_cli(prompt: str, is_instrumental: bool, api_key: str) -> bytes:
    """使用 mmx CLI 生成音乐"""
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        out_path = f.name

    try:
        cmd = [
            "mmx", "music", "generate",
            "--prompt", prompt,
            "--out", out_path,
            "--non-interactive",
        ]
        if is_instrumental:
            cmd.append("--instrumental")

        if api_key:
            cmd.extend(["--api-key", api_key])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            raise APIError("MiniMax", f"mmx music generate failed: {result.stderr}")

        with open(out_path, "rb") as f:
            return f.read()
    finally:
        os.unlink(out_path)


def _generate_bgm_api(prompt: str, api_key: str, is_instrumental: bool, model: str) -> bytes:
    """使用 API 生成音乐（fallback）"""
    api_base = CONFIG.get_minimax("api_base", "https://api.minimaxi.com")
    url = f"{api_base}/v1/music_generation"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "prompt": prompt,
        "is_instrumental": is_instrumental,
        "stream": False,
        "output_format": "hex",
        "audio_setting": {
            "sample_rate": 32000,
            "bitrate": 128000,
            "format": "mp3",
        },
    }

    resp = _post_bgm_request(url, headers, payload, timeout=120)
    resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError as e:
        raise APIError("MiniMax", f"Invalid JSON response: {e} (status={resp.status_code}, body={resp.text[:200]})")

    base_resp = data.get("base_resp", {})
    trace_id = data.get("trace_id")
    if base_resp.get("status_code") != 0:
        MiniMax.error(f"Music API error: {base_resp.get('status_msg')} (trace_id={trace_id})")
        raise APIError("MiniMax", base_resp.get("status_msg"), trace_id)

    audio_hex = data.get("data", {}).get("audio")
    if not audio_hex:
        raise RuntimeError("No audio data in response")

    return binascii.unhexlify(audio_hex)


def generate_bgm_to_file(
    prompt: str,
    api_key: str,
    output_path: str = "output/bgm_generated.mp3",
    is_instrumental: bool = True,
) -> str:
    """
    生成 BGM 并保存到文件

    Returns: 输出文件路径
    """
    audio_bytes = generate_bgm(prompt=prompt, api_key=api_key, is_instrumental=is_instrumental)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(audio_bytes)
    return output_path
