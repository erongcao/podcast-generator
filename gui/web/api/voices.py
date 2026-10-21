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
    浏览器录音 → POST 的 audio cleanup（降噪 / 响度归一）：上传只做格式转码，
    清理走 audio_cleanup.clean_wav_for_cloning，需要时单独跑。
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
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
        # 2026-10-21 起有真列了；老行 clone_status 为空 = 还没克隆过（可重试）
        minimax_voice_id=v.minimax_voice_id or "",
        clone_status=v.clone_status or "",
        clone_error=v.clone_error or "",
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
    clone_status: str = ""   # "" | pending | done | failed | skipped
    clone_error: str = ""
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
    minimax_voice_id: str = ""
    clone_status: str = ""
    clone_error: str = ""


class CloneResponse(BaseModel):
    voice_id: str
    clone_status: str        # done | failed | skipped
    minimax_voice_id: str = ""
    clone_error: str = ""


MINIMAX_VOICE_ID_MIN_LEN = 8
_MINIMAX_INVALID_CHARS = re.compile(r"[^A-Za-z0-9_-]")


def _minimax_server_id(voice_id: str) -> str:
    """
    把本地 voice_id 转成 MiniMax 能接受的服务端 ID。

    **长度下限 8 字符** —— 实测出来的，不是文档里的：
        voice_cloner.clone(wav, "yujialu", key)       -> "invalid params, voice_id length"（7 字符）
        voice_cloner.clone(wav, "yujialu01", key)     -> OK（9 字符）

    库里已有的音色恰好都 ≥ 8 字符（最短的 `cao-test` / `jiang-test` 正好 8），
    所以这条约束一直没被触发，直到录了个 7 字符的 `yujialu`。

    连字符是可用的（`Li-test-v1` 就是靠连字符克隆成功的），所以只清洗字符集，
    不做多余的替换。命名跟历史保持一致：`shanghai-clone-001` / `talarico-clone-001`。
    """
    sid = _MINIMAX_INVALID_CHARS.sub("_", voice_id)
    if len(sid) >= MINIMAX_VOICE_ID_MIN_LEN:
        return sid
    # 补 `_clone` 后仍可能不够长（"_clone" 本身才 6 字符），所以先补够基底长度
    # 再接后缀——否则 "a" 会得到 7 字符的 "a_clone"，照样被 MiniMax 拒。
    suffix = "_clone"
    need = MINIMAX_VOICE_ID_MIN_LEN - len(suffix)
    if len(sid) < need:
        sid += "0" * (need - len(sid))
    return f"{sid}{suffix}"


def _clone_to_minimax(db: Session, row: VoiceClone, api_key: str, timeout: int = 120) -> CloneResponse:
    """
    把一条音色克隆到 MiniMax 云端，结果写回 DB。

    **失败不回滚音色本身** —— 克隆是云端附加能力，ref 本地已经可用了。
    失败时 clone_status='failed' + clone_error，用户可以在界面上重试
    （POST /api/voices/{vid}/clone）。

    幂等：已经有 minimax_voice_id 的行直接返回 done，不重复消耗配额。
    """
    def _finish(status: str, server_id: str = "", error: str = ""):
        row.clone_status = status
        row.minimax_voice_id = server_id or row.minimax_voice_id
        row.clone_error = error or None
        db.commit()
        return CloneResponse(
            voice_id=row.voice_id,
            clone_status=status,
            minimax_voice_id=row.minimax_voice_id or "",
            clone_error=error,
        )

    if not api_key:
        log.info(f"voice {row.voice_id}: skip MiniMax clone (no api_key)")
        return _finish("skipped", error="未配置 MiniMax API key，跳过云端复刻")

    if row.minimax_voice_id:
        return _finish("done")

    wav_path = row.audio_path
    if not wav_path or not Path(wav_path).exists():
        return _finish("failed", error=f"ref 音频不存在: {wav_path}")

    # 服务端 voice_id 沿用本地 ID（库里 10 条历史音色都这么做），但要过一遍
    # _minimax_server_id —— 本地 ID 少于 8 字符时 MiniMax 会拒。
    server_id = _minimax_server_id(row.voice_id)

    row.clone_status = "pending"
    db.commit()

    try:
        import voice_cloner
        returned = voice_cloner.clone(wav_path, server_id, api_key, timeout=timeout)
        return _finish("done", server_id=returned or server_id)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        # MiniMax 不允许同名 voice_id，已存在时报 "voice id duplicate"。
        # 此时 MiniMax 上确实已经有一个可用的复刻音色（目标状态已达成），
        # 当成功处理——否则用户重录同名音色后就永远克隆不了了。
        # 注意：**不会覆盖**服务端已有的音色。clone_error 里留一条说明，
        # 界面上是指针 tooltip，能看到"没覆盖"这件事。
        if "duplicate" in err.lower() or "已存在" in err:
            note = f"MiniMax 上已存在同名复刻音色（{server_id}），未覆盖"
            log.info(f"voice {row.voice_id}: {note}")
            return _finish("done", server_id=server_id, error=note)
        log.warning(f"voice {row.voice_id}: MiniMax clone failed: {err}")
        return _finish("failed", error=err)


def _decode_to_wav(data: bytes, dest: Path) -> None:
    """
    把上传的音频字节流解码成 48kHz mono PCM WAV，写到 dest。

    为什么必须转码（2026-10-21 修）：
    浏览器 MediaRecorder 的产物容器跟扩展名/前端声明的 MIME 经常对不上——
    Safari 在 macOS 上默认录 `audio/mp4`（AAC），Chrome/Firefox 录
    `audio/webm`（opus）。上传路径以前直接 `shutil.copy2` 到 `{vid}.wav`，
    于是一个 AAC/MP4 文件顶着 .wav 后缀进了音色库，事后：
      - `voice_audio_response` 按后缀发 `Content-Type: audio/wav`，浏览器
        <audio> 拿到 WAV content-type + MP4 负载 → 解码失败，试听报错；
      - `wave.open()` → `file does not start with RIFF id`；
      - `soundfile`/libsndfile → `Format not recognised`；
      - VoxCPM2 / MiniMax clone 拿这个 ref 一样跑不起来。

    所以不能信上传文件名，直接把字节丢给 ffmpeg 解。

    Raises:
        RuntimeError: ffmpeg 不在 PATH（503，环境问题）。
        ValueError: ffmpeg 解不出音频（400，用户上传的东西不对）。
    """
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg 未找到，无法转码上传的录音")

    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tmp:
        tmp.write(data)
        src = Path(tmp.name)

    try:
        ff = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(src),
             "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(dest)],
            capture_output=True, text=True, timeout=120,
        )
        if ff.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
            raise ValueError(ff.stderr.strip() or "ffmpeg 转码失败（无法识别的音频格式）")
    finally:
        src.unlink(missing_ok=True)


def add_voice_impl(
    voice_id: str,
    display_name: str,
    file_bytes: bytes,
    notes: str = "",
    ref_text: str = "",
    tags: Optional[List[str]] = None,
) -> AddVoiceResponse:
    """
    上传录音文件，新建一个 voice。

    实际写入流程：
        1. ffmpeg 解码成 48kHz mono PCM WAV → LEGACY_AUDIO_DIR/{voice_id}.wav
        2. 写 VoiceClone 表
        3. 返回 voice_id

    输入不要求是 WAV：浏览器 MediaRecorder 可能给 mp4/aac 或 webm/opus，
    统一在 _decode_to_wav 里转码。
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

    # 1. 解码成真正的 48kHz mono PCM WAV 落到音色库目录。
    #    浏览器录音的容器不可信（Safari = mp4/aac，Chrome = webm/opus），
    #    见 _decode_to_wav 的 docstring。
    dest = LEGACY_AUDIO_DIR / f"{voice_id}.wav"
    try:
        _decode_to_wav(file_bytes, dest)
    except RuntimeError as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"无法解码上传的音频: {e}")

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

        # 4. 顺带克隆到 MiniMax（2026-10-21 新增）
        #    同步执行：单用户本地看板，克隆约几秒，换来"上传完就能直接用"的确定性。
        #    失败不回滚音色——ref 本地已经可用了，clone_status/clone_error 记录原因，
        #    用户可调 POST /api/voices/{vid}/clone 重试。
        from gui.web.api.synthesize import _load_minimax_key
        clone = _clone_to_minimax(db, v, _load_minimax_key())

        return AddVoiceResponse(
            voice_id=v.voice_id,
            display_name=v.display_name,
            has_wav=bool(v.audio_path),
            minimax_voice_id=clone.minimax_voice_id,
            clone_status=clone.clone_status,
            clone_error=clone.clone_error,
        )
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"添加 voice 失败: {e}")
    finally:
        db.close()


def clone_voice_impl(vid: str) -> CloneResponse:
    """
    重试把某条音色克隆到 MiniMax（上传时没配 key / 克隆失败 / 后台补录的用）。

    幂等：已经克隆成功的直接返回 done。
    """
    from gui.web.api.synthesize import _load_minimax_key

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

        return _clone_to_minimax(db, v, _load_minimax_key())
    finally:
        db.close()