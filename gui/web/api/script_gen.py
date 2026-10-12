"""
script_gen.py — Phase 6 脚本生成路由（DB-backed, v0.4.0-web Task 3）

3 个入口：
  1. paste  — 直接粘贴（Phase 1 已有，无需后端）
  2. import — 从 Script 表里挑一个历史脚本
  3. AI    — MiniMax M3 生成（新功能）

API：
  POST /api/script/generate        MiniMax M3 跑生成
  GET  /api/scripts/recent         列历史脚本（最近 20 个）
  POST /api/scripts/save           把当前编辑器内容存盘（写 DB）

Storage:
    Primary: scripts table (per-user, owner_id FK)
    First-run fallback: ~/Library/Application Support/PodcastGenerator/scripts/script_*.json
                       (auto-imported on first /api/scripts/recent call)
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from gui.web.db import SessionLocal
from gui.web.models import User, Script
from gui.web.ws import ws_manager

log = logging.getLogger(__name__)

# 复用 Phase 5 的凭证 helper
from gui.web.api.synthesize import _load_minimax_key, _load_volcengine_creds

# Legacy 脚本目录（PySide6 时代的 storage；DB 迁移后只读）
LEGACY_SCRIPT_DIR = Path.home() / "Library" / "Application Support" / "PodcastGenerator" / "scripts"

# 向后兼容别名：editor.py / tests/test_export.py 仍 import SCRIPT_DIR
SCRIPT_DIR = LEGACY_SCRIPT_DIR


# ============================================================
# Helpers
# ============================================================
def _get_default_owner(db: Session) -> Optional[User]:
    """单用户自托管场景下的默认 owner：第一个用户（优先 admin）。"""
    return (
        db.query(User)
        .filter(User.is_admin == True)  # noqa: E712
        .order_by(User.id)
        .first()
        or db.query(User).order_by(User.id).first()
    )


def _save_script_to_db(segments: List[Dict[str, str]], topic: str,
                       editor_text: str = "") -> dict:
    """写入 DB scripts 表。Returns dict with 'script_id', 'name', 'saved_to'."""
    db = SessionLocal()
    try:
        owner = _get_default_owner(db)
        if owner is None:
            raise RuntimeError(
                "No user exists. Register a user via /api/auth/register first."
            )

        ts = datetime.utcnow()
        editor = editor_text or segments_to_editor_text(segments)
        script = Script(
            owner_id=owner.id,
            topic=topic or "未命名脚本",
            editor_text=editor,
            segments_json=json.dumps(segments, ensure_ascii=False) if segments else None,
            n_segments=sum(1 for s in segments if (s.get("text") or "").strip()),
            created_at=ts,
        )
        db.add(script)
        db.commit()
        db.refresh(script)
        return {
            "script_id": script.id,
            "saved_to": f"script_{script.id}",
            "name": f"script_{script.id}",
        }
    finally:
        db.close()


def _format_for_save(
    segments: List[Dict[str, str]],
    topic: str,
    editor_text: str = "",
) -> str:
    """
    [LEGACY] 写入 ~/Library/.../scripts/script_*.json，返回文件路径。

    保留这个 file-system 函数仅为向后兼容：
      - editor.py 读 SCRIPT_DIR
      - tests/test_export.py 用它创建 fixture
    现在 generate_script_thread 走的是 DB 路径（_save_script_to_db）。

    Deprecated: 用 _save_script_to_db。
    """
    LEGACY_SCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    short = (topic or "未命名主题")[:30].replace("/", "_").replace(" ", "_")
    name = f"script_{ts}_{short}.json"
    p = LEGACY_SCRIPT_DIR / name
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "topic": topic,
        "segments": segments,
        "editor_text": editor_text or segments_to_editor_text(segments),
    }
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(p)


# ============================================================
# 格式化：把 segments → 编辑器可读文本（与 parse 反向）
# ============================================================
def segments_to_editor_text(segments: List[Dict[str, str]]) -> str:
    """
    把 [{"speaker": "主播甲", "text": "..."}, ...] 转成：

        ---

        【主播甲】
        text

        ---

        【主播乙】
        text

    这样的文本，直接粘到编辑器，/api/parse 能 100% 还原。
    """
    parts = []
    for s in segments:
        if not s.get("text", "").strip():
            continue
        parts.append("---\n")
        parts.append(f"【{s['speaker']}】\n")
        parts.append(s["text"].strip() + "\n")
    return "\n".join(parts).rstrip() + "\n"


# ============================================================
# 内部：把 topic / context / custom → chat messages
# ============================================================
def _build_messages_for_topic(
    topic: str, context: str, custom: str, n_speakers: int, show_name: str,
) -> List[Dict[str, str]]:
    from minimax_script_gen import build_messages, DEFAULT_HOSTS_2, DEFAULT_HOSTS_3
    hosts = DEFAULT_HOSTS_2 if n_speakers == 2 else DEFAULT_HOSTS_3
    return build_messages(
        topic=topic,
        host_descriptions=hosts,
        context=context,
        custom=custom,
        show_name=show_name,
    )


# ============================================================
# /api/script/generate
# ============================================================
class ScriptGenRequest(BaseModel):
    topic: str
    context: str = ""
    custom: str = ""
    n_speakers: int = 2
    api_key: str = ""
    show_name: str = "医学科普播客"
    engine: str = "minimax"
    appid: str = ""
    token: str = ""


class ScriptGenResponse(BaseModel):
    script_text: str
    segments: List[Dict[str, str]]
    count: int
    elapsed_sec: float
    saved_to: str = ""


def generate_script_thread(
    topic: str,
    context: str,
    custom: str,
    n_speakers: int,
    api_key: str,
    show_name: str,
    engine: str = "minimax",
    appid: str = "",
    token: str = "",
) -> Optional[ScriptGenResponse]:
    if engine == "doubao":
        return _generate_script_thread_doubao(
            topic, context, custom, appid, token, show_name,
        )
    return _generate_script_thread_minimax(
        topic, context, custom, n_speakers, api_key, show_name,
    )


def _generate_script_thread_minimax(
    topic: str,
    context: str,
    custom: str,
    n_speakers: int,
    api_key: str,
    show_name: str,
) -> Optional[ScriptGenResponse]:
    api_key = (api_key or "").strip() or _load_minimax_key()
    if not api_key:
        ws_manager.push({
            "type": "error",
            "message": "MiniMax api_key 未配置（config.json / env 都没有）",
        })
        return None
    if not topic.strip():
        ws_manager.push({"type": "error", "message": "topic 不能为空"})
        return None

    ws_manager.push({
        "type": "log", "level": "info",
        "message": f"🤖 MiniMax M3 开始生成：主题「{topic[:30]}」({n_speakers} 主持人)",
    })
    ws_manager.push({"type": "progress", "done": 0, "total": 4})

    t0 = time.time()
    try:
        from minimax_script_gen import (
            MiniMaxScriptGenerator,
            DEFAULT_HOSTS_2,
            DEFAULT_HOSTS_3,
        )
        ws_manager.push({"type": "progress", "done": 1, "total": 4})
        gen = MiniMaxScriptGenerator(api_key=api_key)
        hosts = DEFAULT_HOSTS_2 if n_speakers == 2 else DEFAULT_HOSTS_3
        ws_manager.push({"type": "progress", "done": 2, "total": 4})

        ws_manager.push({
            "type": "log", "level": "info",
            "message": "⏳ MiniMax M3 thinking 中（最长 4 分钟）…",
        })

        messages = _build_messages_for_topic(
            topic, context, custom, n_speakers, show_name,
        )
        raw_text = gen._post(messages, max_tokens=None)

        from gui.web.api.parse import parse_script_lines
        try:
            segments = parse_script_lines(raw_text)
        except ValueError as e:
            ws_manager.push({
                "type": "log", "level": "warning",
                "message": f"⚠ 解析失败，回退到 minimax_script_gen 自带 parser: {e}",
            })
            from minimax_script_gen import parse_script
            segments = parse_script(raw_text)
        if not segments:
            raise RuntimeError(
                f"解析后 0 段。LLM 原始输出片段：\n{raw_text[:500]}"
            )

        ws_manager.push({"type": "progress", "done": 3, "total": 4})

        script_text = segments_to_editor_text(segments)
        save_info = _save_script_to_db(segments, topic, script_text)
        saved_to = save_info["saved_to"]
        dt = time.time() - t0
        ws_manager.push({
            "type": "log", "level": "success",
            "message": f"✅ 生成 {len(segments)} 段，耗时 {dt:.1f}s，已存 {save_info['name']}",
        })
        ws_manager.push({"type": "progress", "done": 4, "total": 4})
        ws_manager.push({
            "type": "script_done",
            "script_text": script_text,
            "count": len(segments),
            "elapsed_sec": round(dt, 1),
            "saved_to": saved_to,
        })
        return ScriptGenResponse(
            script_text=script_text,
            segments=segments,
            count=len(segments),
            elapsed_sec=round(dt, 1),
            saved_to=saved_to,
        )
    except Exception as e:
        import traceback
        ws_manager.push({
            "type": "error",
            "message": f"生成失败: {e}",
            "traceback": traceback.format_exc()[-500:],
        })
        return None


# ============================================================
# 豆包 PodcastTTS 脚本生成（preview 模式，只要台词不要音频）
# ============================================================
def _doubao_texts_to_segments(texts: list) -> List[Dict[str, str]]:
    filtered = [
        t for t in texts
        if t.get("round_id") not in (-1, 9999)
        and t.get("speaker") not in ("head_music", "tail_music")
        and (t.get("text") or "").strip()
    ]
    seen = []
    for t in filtered:
        spk = t.get("speaker", "")
        if spk and spk not in seen:
            seen.append(spk)
    name_map = {}
    for i, spk in enumerate(seen[:2]):
        name_map[spk] = "主播甲" if i == 0 else "主播乙"

    out = []
    for t in filtered:
        spk = t.get("speaker", "")
        text = (t.get("text") or "").strip()
        name = name_map.get(spk)
        if not name or not text:
            continue
        out.append({"speaker": name, "text": text})
    return out


def _generate_script_thread_doubao(
    topic: str,
    context: str,
    custom: str,
    appid: str,
    token: str,
    show_name: str,
) -> Optional[ScriptGenResponse]:
    if not (appid or "").strip() or not (token or "").strip():
        creds = _load_volcengine_creds()
        appid = (appid or "").strip() or creds["appid"]
        token = (token or "").strip() or creds["token"]
    if not appid or not token:
        ws_manager.push({
            "type": "error",
            "message": "豆包 APP ID / Token 未配置（config.json / env 都没有）",
        })
        return None
    if not topic.strip():
        ws_manager.push({"type": "error", "message": "topic 不能为空"})
        return None

    ws_manager.push({
        "type": "log", "level": "info",
        "message": f"🤖 豆包 PodcastTTS 开始生成：主题「{topic[:30]}」",
    })
    ws_manager.push({"type": "progress", "done": 0, "total": 3})

    t0 = time.time()
    try:
        text = topic.strip()
        if context.strip():
            text += "\n\n" + context.strip()
        if custom.strip():
            text += "\n\n附加要求：" + custom.strip()
        if show_name and show_name != "医学科普播客":
            text = f"（节目：{show_name}）\n" + text

        ws_manager.push({"type": "progress", "done": 1, "total": 3})
        ws_manager.push({
            "type": "log", "level": "info",
            "message": f"⏳ 连接豆包 WebSocket（input {len(text)} 字，最长 2 分钟）…",
        })

        import asyncio
        from doubao_podcast import generate_podcast
        try:
            result = asyncio.run(generate_podcast(
                text=text,
                output_path="/tmp/doubao_preview_unused.mp3",
                appid=appid,
                token=token,
                only_nlp_text=True,
            ))
        except RuntimeError as e:
            if "asyncio" in str(e) or "loop" in str(e).lower():
                loop = asyncio.new_event_loop()
                try:
                    result = loop.run_until_complete(generate_podcast(
                        text=text,
                        output_path="/tmp/doubao_preview_unused.mp3",
                        appid=appid,
                        token=token,
                        only_nlp_text=True,
                    ))
                finally:
                    loop.close()
            else:
                raise

        ws_manager.push({"type": "progress", "done": 2, "total": 3})

        texts = result.get("texts", []) or []
        segments = _doubao_texts_to_segments(texts)
        if not segments:
            raise RuntimeError(
                f"豆包返回 0 段（raw texts={len(texts)}，可能被 head/tail music 全过滤）"
            )

        script_text = segments_to_editor_text(segments)
        save_info = _save_script_to_db(segments, topic, script_text)
        saved_to = save_info["saved_to"]
        dt = time.time() - t0
        ws_manager.push({
            "type": "log", "level": "success",
            "message": f"✅ 豆包生成 {len(segments)} 段，耗时 {dt:.1f}s，已存 {save_info['name']}",
        })
        ws_manager.push({"type": "progress", "done": 3, "total": 3})
        ws_manager.push({
            "type": "script_done",
            "script_text": script_text,
            "count": len(segments),
            "elapsed_sec": round(dt, 1),
            "saved_to": saved_to,
        })
        return ScriptGenResponse(
            script_text=script_text,
            segments=segments,
            count=len(segments),
            elapsed_sec=round(dt, 1),
            saved_to=saved_to,
        )
    except Exception as e:
        import traceback
        ws_manager.push({
            "type": "error",
            "message": f"豆包生成失败: {e}",
            "traceback": traceback.format_exc()[-500:],
        })
        return None


# ============================================================
# /api/scripts/recent — DB-backed
# ============================================================
class ScriptSummary(BaseModel):
    name: str
    path: str
    created_at: str = ""
    topic: str = ""
    n_segments: int = 0
    preview: str = ""


def _import_legacy_scripts_on_demand(db: Session, owner_id: int) -> List[Script]:
    """
    First-run fallback: import script_*.json → scripts table.

    Only runs when scripts table is empty for this owner AND
    legacy SCRIPT_DIR exists.
    """
    if db.query(Script).filter(Script.owner_id == owner_id).count() > 0:
        return []
    if not LEGACY_SCRIPT_DIR.exists():
        return []

    files = sorted(LEGACY_SCRIPT_DIR.glob("script_*.json"))
    if not files:
        return []

    rows: List[Script] = []
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue

        topic = (data.get("topic") or "未命名脚本").strip() or "未命名脚本"
        segments = data.get("segments") or []
        editor_text = data.get("editor_text") or ""
        if not editor_text and segments:
            editor_text = segments_to_editor_text(segments)

        # 解析 created_at（兼容 +00:00 / Z / naive）
        ts = datetime.utcnow()
        if data.get("created_at"):
            try:
                s = data["created_at"].replace("Z", "+00:00")
                ts = datetime.fromisoformat(s)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=None)  # 存 naive，DB 列是 DateTime(timezone=True)
            except (ValueError, AttributeError):
                pass

        # Idempotency: same (owner, topic, created_at) tuple
        existing = (
            db.query(Script)
            .filter(
                Script.owner_id == owner_id,
                Script.topic == topic,
                Script.created_at == ts,
            )
            .first()
        )
        if existing:
            continue

        s = Script(
            owner_id=owner_id,
            topic=topic,
            editor_text=editor_text,
            segments_json=json.dumps(segments, ensure_ascii=False) if segments else None,
            n_segments=sum(1 for x in segments if isinstance(x, dict) and (x.get("text") or "").strip()),
            created_at=ts,
        )
        db.add(s)
        rows.append(s)

    if rows:
        try:
            db.commit()
            log.info(f"Imported {len(rows)} legacy scripts from {LEGACY_SCRIPT_DIR}")
        except Exception as e:
            log.warning(f"Legacy scripts commit failed: {e}")
            db.rollback()
            return []

    return rows


def _script_row_to_summary(s: Script) -> ScriptSummary:
    """ORM row → API response. 'path' 字段用 script_{id} 兼容旧前端契约。"""
    segments: List[dict] = []
    if s.segments_json:
        try:
            segments = json.loads(s.segments_json)
        except Exception:
            segments = []
    preview = ""
    if segments:
        first_text = segments[0].get("text", "") if isinstance(segments[0], dict) else ""
        preview = first_text[:80] + ("..." if len(first_text) > 80 else "")
    name = f"script_{s.id}"
    return ScriptSummary(
        name=name,
        path=name,           # 兼容旧前端：path.split('/').pop() === name
        created_at=s.created_at.isoformat() if s.created_at else "",
        topic=s.topic or "",
        n_segments=s.n_segments or 0,
        preview=preview,
    )


def list_recent_scripts(limit: int = 20) -> List[ScriptSummary]:
    """DB-backed. First-run fallback: import legacy JSON files."""
    db = SessionLocal()
    try:
        owner = _get_default_owner(db)
        if owner is None:
            return []

        rows = (
            db.query(Script)
            .filter(Script.owner_id == owner.id)
            .order_by(Script.created_at.desc())
            .limit(limit)
            .all()
        )

        if not rows:
            # First-run fallback
            rows = _import_legacy_scripts_on_demand(db, owner.id)

        return [_script_row_to_summary(s) for s in rows]
    finally:
        db.close()


# ============================================================
# /api/scripts/save — DB write
# ============================================================
class ScriptSaveRequest(BaseModel):
    script_text: str
    topic: str = ""


def save_script_impl(script_text: str, topic: str = "") -> dict:
    """把当前编辑器内容存到 DB。返回 {saved_to, name}（兼容旧 API 响应）。"""
    db = SessionLocal()
    try:
        owner = _get_default_owner(db)
        if owner is None:
            raise HTTPException(
                status_code=503,
                detail="没有可用 owner。请先 /api/auth/register。",
            )

        ts = datetime.utcnow()
        script = Script(
            owner_id=owner.id,
            topic=(topic or "手动保存").strip() or "手动保存",
            editor_text=script_text,
            segments_json=None,  # 用户保存时还没 parse，留空
            n_segments=0,
            created_at=ts,
        )
        db.add(script)
        db.commit()
        db.refresh(script)
        name = f"script_{script.id}"
        return {"saved_to": name, "name": name}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"保存脚本失败: {e}")
    finally:
        db.close()


# ============================================================
# /api/scripts/get — 读单个历史脚本（by name）
# ============================================================
def load_script_impl(name: str) -> dict:
    """
    读 DB 里单个 script。name 形如 "script_42"。

    向后兼容：旧 JSON 文件（script_YYYYMMDD_HHMMSS_topic.json）若 DB 里没有，
    尝试从 LEGACY_SCRIPT_DIR 直接读（first-run 过渡期）。
    """
    if not name:
        raise ValueError(f"非法文件名: {name!r}")
    # 阻止路径穿越
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError(f"文件名含路径分隔符: {name!r}")

    db = SessionLocal()
    try:
        owner = _get_default_owner(db)

        # 1. DB lookup by id (primary path)
        if owner is not None and name.startswith("script_") and not name.endswith(".json"):
            try:
                script_id = int(name[len("script_"):])
                s = (
                    db.query(Script)
                    .filter(Script.owner_id == owner.id, Script.id == script_id)
                    .first()
                )
                if s is not None:
                    segments = []
                    if s.segments_json:
                        try:
                            segments = json.loads(s.segments_json)
                        except Exception:
                            segments = []
                    return {
                        "created_at": s.created_at.isoformat() if s.created_at else "",
                        "topic": s.topic,
                        "editor_text": s.editor_text,
                        "segments": segments,
                    }
            except ValueError:
                pass  # name 不是纯数字 → fallthrough to legacy

        # 2. Legacy fallback：旧的 script_*.json 文件还在磁盘上
        if name.startswith("script_") and name.endswith(".json"):
            legacy_path = LEGACY_SCRIPT_DIR / name
            if legacy_path.exists():
                try:
                    return json.loads(legacy_path.read_text(encoding="utf-8"))
                except Exception as e:
                    raise FileNotFoundError(f"{name}: legacy read failed: {e}")

        raise FileNotFoundError(name)
    finally:
        db.close()