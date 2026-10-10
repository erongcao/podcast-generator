#!/usr/bin/env python3
"""
MiniMax voice cloning API client (two-step form).

Verified end-to-end against the user's MiniMax account (api.minimaxi.com, cn region).
The /v1/voice_clone endpoint is a two-step process:

  Step 1: POST https://api.minimaxi.com/v1/files/upload
          Headers: Authorization: Bearer <api_key>
          Body (multipart/form-data):
            purpose = "voice_clone"
            file    = <WAV/MP3/M4A binary>
          Success response:
            {"file": {"file_id": <int>, "bytes": <int>, "created_at": <int>, ...},
             "base_resp": {"status_code": 0, "status_msg": "success"}}

  Step 2: POST https://api.minimaxi.com/v1/voice_clone
          Headers: Authorization: Bearer <api_key>
                   Content-Type: application/json
          Body (JSON): {"file_id": <int>, "voice_id": <str>}
          Success response:
            {"input_sensitive": bool, "demo_audio": str,
             "base_resp": {"status_code": 0, "status_msg": "success"}, ...}

  Error codes (base_resp.status_code; 0 = success):
    1002  rate-limited ("触发限流，请稍后再试")
    1004  account auth failed ("账号鉴权失败")
    1008  insufficient balance ("账号余额不足")
    2013  invalid params ("传入参数异常")
    2038  voice clone forbidden (account not verified)
    2049  invalid api key

  voice_id constraints (verified against API):
    - 8 to 256 characters total
    - must start with a letter (a-zA-Z)
    - remaining chars: a-z A-Z 0-9 _ -

  No documented endpoint exists for deleting a cloned voice_id; MiniMax
  auto-deletes unused clones after 7 days.
"""
from pathlib import Path

import requests

import minimax_tts
from exceptions import VoiceCloneError
from network_retry import retry_on_transient

UPLOAD_URL = "https://api.minimaxi.com/v1/files/upload"
CLONE_URL = "https://api.minimaxi.com/v1/voice_clone"


@retry_on_transient()
def _post_upload_request(url, headers, files, data, timeout):
    return requests.post(url, headers=headers, files=files, data=data, timeout=timeout)


@retry_on_transient()
def _post_clone_request(url, headers, json_body, timeout):
    return requests.post(url, headers=headers, json=json_body, timeout=timeout)


def clone(wav_path, voice_id, api_key, timeout=60):
    """
    上传 WAV 到 MiniMax 创建复刻音色（两步流程）。

    1. 上传到 /v1/files/upload 获取 file_id (int)
    2. 调用 /v1/voice_clone (JSON) 完成克隆

    返回 voice_id；失败抛 VoiceCloneError。
    """
    wav_path = Path(wav_path)
    if not wav_path.exists():
        raise VoiceCloneError(f"WAV 文件不存在: {wav_path}")

    # Step 1: upload audio file → file_id
    with open(wav_path, "rb") as f:
        upload_resp = _post_upload_request(
            UPLOAD_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (wav_path.name, f, "audio/wav")},
            data={"purpose": "voice_clone"},
            timeout=timeout,
        )
    if upload_resp.status_code != 200:
        raise VoiceCloneError(
            f"Upload HTTP {upload_resp.status_code}: {upload_resp.text[:200]}",
            status_code=upload_resp.status_code,
        )
    try:
        upload_data = upload_resp.json()
    except ValueError as e:
        raise VoiceCloneError(f"Upload invalid JSON: {e}")
    if upload_data.get("base_resp", {}).get("status_code", -1) != 0:
        raise VoiceCloneError(
            f"Upload API 错误: {upload_data.get('base_resp', {}).get('status_msg', 'unknown')}",
        )
    file_id = upload_data.get("file", {}).get("file_id")
    if not file_id:
        raise VoiceCloneError(f"Upload response missing file_id: {upload_data}")

    # Step 2: create clone with JSON body containing int file_id
    clone_resp = _post_clone_request(
        CLONE_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json_body={"file_id": file_id, "voice_id": voice_id},
        timeout=timeout,
    )
    if clone_resp.status_code != 200:
        raise VoiceCloneError(
            f"Clone HTTP {clone_resp.status_code}: {clone_resp.text[:200]}",
            status_code=clone_resp.status_code,
        )
    try:
        clone_data = clone_resp.json()
    except ValueError as e:
        raise VoiceCloneError(f"Clone invalid JSON: {e}")
    if clone_data.get("base_resp", {}).get("status_code", -1) != 0:
        raise VoiceCloneError(
            f"Clone API 错误: {clone_data.get('base_resp', {}).get('status_msg', 'unknown')}",
            status_code=clone_resp.status_code,
            trace_id=clone_data.get("trace_id"),
        )
    return clone_data.get("voice_id", voice_id)


def synthesize_test(voice_id, text, api_key):
    """
    用指定 voice_id 合成一段测试音频。委托给 minimax_tts.synthesize。
    返回 mp3 bytes。
    """
    return minimax_tts.synthesize(
        text=text,
        voice_id=voice_id,
        api_key=api_key,
    )


def synthesize_test_voxcpm(voice_id, text):
    """
    用本地 VoxCPM2 合成一段测试音频。从 voice_library 读 wav_path。
    VoxCPM2 不需要 ref_text（仅靠 ref_audio 即可克隆），
    所以 Li-test-v1.qwen3.wav 等任意干净片段都可用。

    Args:
        voice_id: 音色库里的 voice_id
        text: 待合成文本

    Returns:
        mp3 bytes

    Raises:
        VoiceCloneError: 找不到记录、缺 wav_path、合成失败
    """
    import voice_library as vl
    import voxcpm_tts
    from exceptions import LibraryError

    record = vl.get(voice_id)
    if record is None:
        raise VoiceCloneError(f"音色库中不存在 voice_id={voice_id}")

    wav_path = record.get("qwen3_wav_path") or record.get("wav_path")
    if not wav_path:
        raise VoiceCloneError(f"voice_id={voice_id} 没有 wav_path，无法用 VoxCPM2 合成")

    try:
        return voxcpm_tts.synthesize(
            text=text,
            wav_path=wav_path,
            ref_text=None,
        )
    except LibraryError:
        raise
    except Exception as e:
        raise VoiceCloneError(f"VoxCPM2 合成失败: {e}")
