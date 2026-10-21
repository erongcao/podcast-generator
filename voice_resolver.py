"""
voice_resolver.py — 音色查找的统一入口（2026-10-21）

**为什么需要这个模块**

v0.4.0 把音色存储搬到了 SQLite（`voice_clones` 表），但 TTS 引擎这侧没跟上：
`voxcpm_tts._resolve_wav()` 和 `minimax_tts` 的 ID 转换都只查遗留 JSON
（`voice_library.get()`）。于是**从 Web 看板录的音色在引擎眼里根本不存在**：

    User 在看板录音 → add_voice_impl 写进 SQLite 的 voice_clones
    → 合成时 voxcpm_tts 查 voice_library.get("yujialu") → None
    → VoiceCloneError: 音色库中不存在 voice_id=yujialu

（DB 只在第一次 `/api/voices` 时把遗留 JSON 导入一遍，之后新增的音色两边不通。
JSON 里还有 `minimax_voice_id` 字段，DB 里没有对应列，所以 DB-only 音色在
MiniMax 侧还会退化成"把本地 ID 原样发给 MiniMax" → voice id not exist。）

**本模块的策略**

两个存储都查，先遗留 JSON 后 SQLite，返回归一化后的 dict，让引擎只认一种形状。
选这个方向而不是"把 JSON 迁干净就完事"的理由：CLI / 旧 Qt 面板仍然直接写
`voice_library`，硬砍会让那条路断掉；两边都查则两边都活。

DB 访问一律防御式——CLI 环境下数据库可能根本不存在或没建表，此时静默退回
JSON-only 行为，不影响既有流程。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# voice_clones 原本没有 minimax_voice_id 列，2026-10-21 补上了（models.py:121 +
# app.py::_ensure_voice_clone_columns 负责给老库 ALTER）。
# 老行该列仍为 NULL —— 那表示"还没克隆过"，不是"服务端 ID 就等于本地 ID"。
# 查不到映射时返回 None，让调用方退回原始 ID 并给出可读的错误。


def _normalize(record: dict) -> dict:
    """把一条记录补齐成引擎认得的形状（缺失字段给空值，不改已有值）。"""
    out = dict(record)
    out.setdefault("display_name", out.get("voice_id", ""))
    out.setdefault("notes", "")
    out.setdefault("ref_text", "")
    out.setdefault("tags", [])
    return out


def _from_legacy_json(voice_id: str) -> Optional[dict]:
    """查遗留 JSON 库。"""
    try:
        import voice_library as vl
    except Exception as e:  # pragma: no cover - voice_library 一直都在
        log.debug("voice_resolver: 导入 voice_library 失败: %s", e)
        return None
    try:
        record = vl.get(voice_id)
    except Exception as e:
        log.warning("voice_resolver: voice_library.get(%r) 失败: %s", voice_id, e)
        return None
    return _normalize(record) if record else None


def _from_db(voice_id: str) -> Optional[dict]:
    """查 SQLite voice_clones 表。任何异常都吞掉——DB 不可用不该拖垮合成。"""
    try:
        from gui.web.auth import get_default_owner
        from gui.web.db import SessionLocal
        from gui.web.models import VoiceClone
    except Exception as e:
        log.debug("voice_resolver: DB 模块不可用: %s", e)
        return None

    db = None
    try:
        db = SessionLocal()
        owner = get_default_owner(db)
        row = (
            db.query(VoiceClone)
            .filter(VoiceClone.owner_id == owner.id, VoiceClone.voice_id == voice_id)
            .first()
        )
        if row is None:
            return None
        return _normalize({
            "voice_id": row.voice_id,
            "display_name": row.display_name or row.voice_id,
            "ref_text": row.ref_text or "",
            "notes": row.notes or "",
            "tags": list(row.tags or []),
            "wav_path": row.audio_path or "",
            "minimax_voice_id": getattr(row, "minimax_voice_id", None) or "",
            "_source": "db",
        })
    except Exception as e:
        # 没有 owner / 表不存在 / DB 文件损坏 —— 都退化成"这条查不到"
        log.warning("voice_resolver: DB 查询 voice_id=%r 失败: %s", voice_id, e)
        return None
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


def resolve(voice_id: str) -> Optional[dict]:
    """
    查一条音色记录，先遗留 JSON 后 SQLite。

    Returns:
        dict 或 None。归一化后的字段：
        ``voice_id / display_name / notes / ref_text / tags / wav_path /
        minimax_voice_id``（JSON 记录额外保留 ``qwen3_wav_path``、``preview_path``
        等原始键）。
    """
    if not voice_id:
        return None
    return _from_legacy_json(voice_id) or _from_db(voice_id)


def resolve_wav(voice_id: str):
    """
    取合成用的 (wav_path, ref_text)。

    优先用清洗过的版本（``audio_cleanup.clean_wav_for_cloning`` 产出）—— 与原 wav
    同目录同 stem，48kHz / -16 LUFS，避免不同音色间底噪 / 混响 / 电平不一致。

    Raises:
        VoiceCloneError: 找不到该 voice_id，或记录里没有可用音频。
    """
    from exceptions import VoiceCloneError

    record = resolve(voice_id)
    if record is None:
        raise VoiceCloneError(f"音色库中不存在 voice_id={voice_id}")

    raw_path = record.get("qwen3_wav_path") or record.get("wav_path")
    if not raw_path:
        raise VoiceCloneError(f"voice_id={voice_id} 缺 wav_path")

    cleaned_path = Path(raw_path).with_name(Path(raw_path).stem + ".cleaned.wav")
    wav_path = str(cleaned_path) if cleaned_path.exists() else raw_path
    return wav_path, (record.get("ref_text") or "").strip() or None


def resolve_minimax_voice_id(local_voice_id: str) -> Optional[str]:
    """
    把本地 voice_id 映射到 MiniMax 服务端 voice_id。

    用户在 GUI / 看板填的是本地 ID（如 ``talarico-james``），但 MiniMax
    ``/v1/t2a_v2`` 要服务端 ID（如 ``talarico-clone-001``）。

    查不到映射时返回 None，调用方应退回原始 ID 保持向后兼容。
    """
    record = resolve(local_voice_id)
    if not record:
        return None
    if record.get("_source") == "db" and not (record.get("minimax_voice_id") or "").strip():
        # DB 行还没克隆到 MiniMax → 没有服务端 ID 可映射。返回 None 而不是
        # 拿本地 ID 硬凑，那样只会换来 MiniMax 的 "voice id not exist"。
        log.info(
            f"voice {local_voice_id}: 尚未克隆到 MiniMax"
            "（POST /api/voices/%s/clone 可补）",
            local_voice_id,
        )
        return None
    return (record.get("minimax_voice_id") or "").strip() or None