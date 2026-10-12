"""
voices.py — 音色库路由（DB-backed, v0.4.0-web Task 3）

Phase 5 范围：
    GET  /api/voices              列出所有 voice
    GET  /api/voices/{vid}/audio  试听 ref audio（mp3/wav 直接 stream）
    POST /api/voices/{vid}/delete 删除 voice
    POST /api/voices              上传新 voice (multipart)

Storage:
    Primary: voice_clones table (per-user, owner_id FK)
    First-run fallback: ~/Library/Application Support/PodcastGenerator/voices/voices.json
                       (auto-imported once on first /api/voices call)

不在 Phase 5 范围（先不做）：
    POST /api/voices (新增 / 上传) — 需要 multipart 复杂逻辑；Phase 7+
    浏览器录音 → POST — 涉及文件上传 + audio cleanup；Phase 7+
"""
from __future__ import annotations

import logging
import re
import shutil
import tempfile
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from gui.web.auth import get_default_owner
from gui.web.db import SessionLocal
from gui.web.models import VoiceClone, utcnow

log = logging.getLogger(__name__)

# 音频文件目录（保留 PySide6 时代的位置，便于复用已有 wav/mp3）
LEGACY_AUDIO_DIR = Path.home() / "Library" / "Application Support" / "PodcastGenerator" / "voices" / "audio"
LEGACY_VOICES_JSON = Path.home() / "Library" / "Application Support" / "PodcastGenerator" / "voices" / "voices.json"
LEGACY_AUDIO_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# Helpers
# ============================================================
def _safe_resolve_audio_path(p: Path) -> Optional[Path]:
    """Defense-in-depth: audio files must live under LEGACY_AUDIO_DIR.

    Returns the resolved Path if it stays inside LEGACY_AUDIO_DIR,
    otherwise None (caller returns 404). This guards against
    malformed DB rows whose audio_path was set to /etc/passwd or similar.
    """
    try:
        # resolve() also follows symlinks; compare to base dir
        resolved = p.resolve()
        base = LEGACY_AUDIO_DIR.resolve()
        if not resolved.is_relative_to(base):
            log.warning(
                f"audio_path {p} resolves to {resolved} outside LEGACY_AUDIO_DIR {base}"
            )
            return None
        return resolved if resolved.exists() else None
    except (OSError, ValueError):
        return None


def _to_summary(v: VoiceClone) -> "VoiceSummary":
    """ORM row → API response model. Backward-compat field mapping."""
    # Source detection from tags (matches voices.json "source": "voice_design")
    source = ""
    if "voice_design" in (v.tags or []):
        source = "voice_design"
    elif v.audio_path and v.audio_path.endswith(".preview.mp3"):
        source = "voice_design"
    elif v.audio_path and v.audio_path.endswith(".wav"):
        source = "voice_clone"

    has_wav = bool(v.audio_path) and v.audio_path.endswith(".wav")
    has_preview = bool(v.audio_path) and v.audio_path.endswith(".mp3")

    return VoiceSummary(
        voice_id=v.voice_id,
        display_name=v.display_name or v.voice_id,
        notes=v.notes or "",
        tags=list(v.tags or []),
        has_wav=has_wav,
        has_preview=has_preview,
        minimax_voice_id=v.voice_id,  # historical: voice_id == minimax ID for cloned ones
        source=source,
        created_at=v.created_at.isoformat() if v.created_at else "",
    )


def _import_legacy_voices_on_demand(db: Session, owner_id: int) -> List[VoiceClone]:
    """
    First-run fallback: import voices.json → voice_clones.

    Only runs when:
      - voice_clones is empty for this owner
      - voices.json exists on disk

    Idempotent: per-entry skip if voice_id already exists.
    Returns the resulting list of VoiceClone rows.
    """
    if db.query(VoiceClone).filter(VoiceClone.owner_id == owner_id).count() > 0:
        return []

    if not LEGACY_VOICES_JSON.exists():
        return []

    try:
        import json
        data = json.loads(LEGACY_VOICES_JSON.read_text(encoding="utf-8"))
        voices = data.get("voices") or []
    except Exception as e:
        log.warning(f"voices.json read failed: {e}")
        return []

    rows: List[VoiceClone] = []
    for entry in voices:
        if not isinstance(entry, dict):
            continue
        vid = entry.get("voice_id")
        if not vid or not isinstance(vid, str):
            continue

        existing = (
            db.query(VoiceClone)
            .filter(VoiceClone.owner_id == owner_id, VoiceClone.voice_id == vid)
            .first()
        )
        if existing:
            continue

        # audio_path: wav_path 优先（voice_clone），fallback preview_path（voice_design）
        audio_path = entry.get("wav_path") or entry.get("preview_path")

        # created_at 解析（兼容 +00:00 / Z）
        created_at = None
        if entry.get("created_at"):
            try:
                s = entry["created_at"].replace("Z", "+00:00")
                created_at = datetime.fromisoformat(s)
            except (ValueError, AttributeError):
                pass

        v = VoiceClone(
            owner_id=owner_id,
            voice_id=vid,
            display_name=entry.get("display_name") or vid,
            ref_text=entry.get("ref_text"),
            notes=entry.get("notes"),
            tags=list(entry.get("tags") or []),
            audio_path=audio_path,
            created_at=created_at,
        )
        db.add(v)
        rows.append(v)

    if rows:
        try:
            db.commit()
            log.info(f"Imported {len(rows)} legacy voices from voices.json")
        except Exception as e:
            log.warning(f"Legacy voices commit failed: {e}")
            db.rollback()
            return []

    return rows


# ============================================================
# Schemas
# ============================================================
class VoiceSummary(BaseModel):
    voice_id: str
    display_name: str
    notes: str = ""
    tags: List[str] = []
    has_wav: bool = False
    has_preview: bool = False
    minimax_voice_id: str = ""
    source: str = ""
    created_at: str = ""


class VoiceListResponse(BaseModel):
    voices: List[VoiceSummary]
    total: int


# ============================================================
# /api/voices
# ============================================================
def list_voices_impl() -> VoiceListResponse:
    """列出所有 voice，给前端下拉用。

    Strategy:
      1. Look up default owner (first user).
      2. Query voice_clones.
      3. If empty AND voices.json exists, auto-import on first call.
      4. If still empty (no JSON, no DB rows), return empty list.
    """
    db = SessionLocal()
    try:
        try:
            owner = get_default_owner(db)
        except RuntimeError:
            return VoiceListResponse(voices=[], total=0)

        rows = (
            db.query(VoiceClone)
            .filter(VoiceClone.owner_id == owner.id)
            .order_by(VoiceClone.created_at.desc())
            .all()
        )

        if not rows:
            # First-run fallback
            rows = _import_legacy_voices_on_demand(db, owner.id)

        return VoiceListResponse(
            voices=[_to_summary(r) for r in rows],
            total=len(rows),
        )
    finally:
        db.close()


# ============================================================
# /api/voices/{vid}/audio
# ============================================================
def _find_audio_path(vid: str) -> Optional[Path]:
    """优先 audio_path（mp3/wav 直接 stream）。"""
    db = SessionLocal()
    try:
        try:
            owner = get_default_owner(db)
        except RuntimeError:
            return None
        v = (
            db.query(VoiceClone)
            .filter(VoiceClone.owner_id == owner.id, VoiceClone.voice_id == vid)
            .first()
        )
        if v is None or not v.audio_path:
            return None
        p = Path(v.audio_path)
        return _safe_resolve_audio_path(p)
    finally:
        db.close()


def voice_audio_response(vid: str):
    """GET 试听。返回 FileResponse 让浏览器 <audio> 直接 src=... 播放。"""
    p = _find_audio_path(vid)
    if p is None:
        raise HTTPException(
            status_code=404,
            detail=f"voice {vid!r} 没有可试听音频（DB 里 audio_path 不存在或文件缺失）",
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
    db = SessionLocal()
    try:
        try:
            owner = get_default_owner(db)
        except RuntimeError:
            raise HTTPException(status_code=404, detail=f"voice_id 不存在: {vid}")

        v = (
            db.query(VoiceClone)
            .filter(VoiceClone.owner_id == owner.id, VoiceClone.voice_id == vid)
            .first()
        )
        if v is None:
            raise HTTPException(status_code=404, detail=f"voice_id 不存在: {vid}")

        # 删除磁盘上的 wav / mp3
        if v.audio_path:
            p = Path(v.audio_path)
            if p.exists():
                try:
                    p.unlink()
                except OSError as e:
                    log.warning(f"Failed to delete audio file {p}: {e}")

        db.delete(v)
        db.commit()
        return DeleteResponse(deleted=True, voice_id=vid)
    finally:
        db.close()


# ============================================================
# POST /api/voices — 上传新 voice
# ============================================================
VOICE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class AddVoiceResponse(BaseModel):
    voice_id: str
    display_name: str
    has_wav: bool


def add_voice_impl(
    voice_id: str,
    display_name: str,
    file_bytes: bytes,
    notes: str = "",
    ref_text: str = "",
    tags: Optional[List[str]] = None,
) -> AddVoiceResponse:
    """
    上传 WAV 文件，新建一个 voice。

    实际写入流程：
        1. 写临时文件（multipart 已经读到内存）
        2. 复制到 LEGACY_AUDIO_DIR / {voice_id}.wav
        3. 写 VoiceClone 表
        4. 返回 voice_id
    """
    if not VOICE_ID_PATTERN.match(voice_id):
        raise HTTPException(
            status_code=400,
            detail=f"voice_id 非法（只允许字母数字下划线连字符，1-64 字符）: {voice_id!r}",
        )
    if not file_bytes:
        raise HTTPException(status_code=400, detail="上传的音频文件为空")
    if len(file_bytes) > 100 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件超过 100MB 限制")

    # 1. 临时文件（HTTP 上传已经在内存里，落到磁盘再原子复制）
    suffix = ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    # 2. 复制到音频目录
    dest = LEGACY_AUDIO_DIR / f"{voice_id}{suffix}"
    try:
        shutil.copy2(tmp_path, dest)
    except Exception as e:
        Path(tmp_path).unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"写入音频文件失败: {e}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    # 3. 写 DB
    db = SessionLocal()
    try:
        try:
            owner = get_default_owner(db)
        except RuntimeError:
            # 没有 owner：回滚文件
            dest.unlink(missing_ok=True)
            raise HTTPException(
                status_code=503,
                detail="没有可用的 owner 账号。请先 /api/auth/register 注册一个用户。",
            )

        # 唯一性检查
        existing = (
            db.query(VoiceClone)
            .filter(VoiceClone.owner_id == owner.id, VoiceClone.voice_id == voice_id)
            .first()
        )
        if existing:
            dest.unlink(missing_ok=True)
            raise HTTPException(status_code=409, detail=f"voice_id 已存在: {voice_id}")

        v = VoiceClone(
            owner_id=owner.id,
            voice_id=voice_id,
            display_name=display_name or voice_id,
            ref_text=ref_text or None,
            notes=notes or None,
            tags=list(tags or []),
            audio_path=str(dest),
            created_at=utcnow(),
        )
        db.add(v)
        db.commit()
        return AddVoiceResponse(
            voice_id=v.voice_id,
            display_name=v.display_name,
            has_wav=bool(v.audio_path),
        )
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"添加 voice 失败: {e}")
    finally:
        db.close()