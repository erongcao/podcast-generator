"""
voices.py — 音色库路由

Phase 5 范围：
    GET  /api/voices              列出所有 voice
    GET  /api/voices/{vid}/audio  试听 ref audio（mp3/wav 直接 stream）
    POST /api/voices/{vid}/delete 删除 voice

不在 Phase 5 范围（先不做）：
    POST /api/voices (新增 / 上传) — 需要 multipart 复杂逻辑；Phase 7+
    浏览器录音 → POST — 涉及文件上传 + audio cleanup；Phase 7+
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

log = logging.getLogger(__name__)

# 复用 voice_library.py（项目根的，不动它）
import sys
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import voice_library as vl  # noqa: E402


# ============================================================
# Schemas
# ============================================================
class VoiceSummary(BaseModel):
    voice_id: str
    display_name: str
    notes: str = ""
    tags: List[str] = []
    has_wav: bool = False           # 本地有 ref wav（VoxCPM/Qwen3 路径用得到）
    has_preview: bool = False       # voice_design 生成的预览 mp3
    minimax_voice_id: str = ""      # MiniMax 服务端 ID（如果有）
    source: str = ""                # "voice_clone" / "voice_design" / ""
    created_at: str = ""


class VoiceListResponse(BaseModel):
    voices: List[VoiceSummary]
    total: int


# ============================================================
# /api/voices
# ============================================================
def list_voices_impl() -> VoiceListResponse:
    """列出所有 voice，给前端下拉用。"""
    vl.init()
    raw = vl.list_all()
    out = []
    for v in raw:
        out.append(VoiceSummary(
            voice_id=v.get("voice_id", ""),
            display_name=v.get("display_name", ""),
            notes=v.get("notes", ""),
            tags=list(v.get("tags", []) or []),
            has_wav=bool(v.get("wav_path")),
            has_preview=bool(v.get("preview_path")),
            minimax_voice_id=v.get("minimax_voice_id", "") or "",
            source=v.get("source", "") or "",
            created_at=v.get("created_at", "") or "",
        ))
    return VoiceListResponse(voices=out, total=len(out))


# ============================================================
# /api/voices/{vid}/audio
# ============================================================
def _find_audio_path(vid: str) -> Path | None:
    """优先 preview_path（mp3，浏览器原生支持），其次 wav_path。"""
    rec = vl.get(vid)
    if rec is None:
        return None
    for key in ("preview_path", "wav_path"):
        p = rec.get(key)
        if p and Path(p).exists():
            return Path(p)
    return None


def voice_audio_response(vid: str):
    """GET 试听。返回 FileResponse 让浏览器 <audio> 直接 src=... 播放。"""
    p = _find_audio_path(vid)
    if p is None:
        raise HTTPException(
            status_code=404,
            detail=f"voice {vid!r} 没有可试听音频（既无 preview 也无 wav）",
        )
    media_type = "audio/mpeg" if p.suffix.lower() == ".mp3" else "audio/wav"
    return FileResponse(str(p), media_type=media_type, filename=p.name)


# ============================================================
# /api/voices/{vid}/delete
# ============================================================
class DeleteResponse(BaseModel):
    deleted: bool
    voice_id: str


def delete_voice_impl(vid: str) -> DeleteResponse:
    if not vl.exists(vid):
        raise HTTPException(status_code=404, detail=f"voice_id 不存在: {vid}")
    vl.delete(vid)
    return DeleteResponse(deleted=True, voice_id=vid)


# ============================================================
# POST /api/voices — 上传新 voice
# ============================================================
import re

VOICE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class AddVoiceResponse(BaseModel):
    voice_id: str
    display_name: str
    has_wav: bool


def add_voice_impl(voice_id: str, display_name: str, file_bytes: bytes,
                   notes: str = "", ref_text: str = "",
                   tags: list[str] | None = None) -> AddVoiceResponse:
    """
    上传 WAV 文件，新建一个 voice。

    实际写入流程：
        1. 写到临时文件
        2. voice_library.add(voice_id, display_name, tmp_path, notes, ref_text, tags)
           - add() 会自动把 wav 复制到 ~/Library/Application Support/PodcastGenerator/voices/audio/
           - 然后追加到 voices.json 索引
        3. 返回 voice_id
    """
    if not VOICE_ID_PATTERN.match(voice_id):
        raise HTTPException(
            status_code=400,
            detail=f"voice_id 非法（只允许字母数字下划线连字符，1-64 字符）: {voice_id!r}",
        )
    if vl.exists(voice_id):
        raise HTTPException(status_code=409, detail=f"voice_id 已存在: {voice_id}")
    if not file_bytes:
        raise HTTPException(status_code=400, detail="上传的音频文件为空")
    if len(file_bytes) > 100 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件超过 100MB 限制")

    # 写临时文件
    import tempfile
    suffix = ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        rec = vl.add(
            voice_id=voice_id,
            display_name=display_name or voice_id,
            wav_path=tmp_path,
            notes=notes,
            ref_text=ref_text,
            tags=tags or [],
        )
        return AddVoiceResponse(
            voice_id=rec["voice_id"],
            display_name=rec["display_name"],
            has_wav=bool(rec.get("wav_path")),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"添加 voice 失败: {e}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)
