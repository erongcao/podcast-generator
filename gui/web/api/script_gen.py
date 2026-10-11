"""
script_gen.py — Phase 6 脚本生成路由

3 个入口：
  1. paste  — 直接粘贴（Phase 1 已有，无需后端）
  2. import — 从 ~/Library/Application Support/PodcastGenerator/scripts/ 历史里挑一个
  3. AI    — MiniMax M3 生成（新功能）

API：
  POST /api/script/generate        MiniMax M3 跑生成
  GET  /api/scripts/recent         列历史脚本（最近 20 个）
  POST /api/scripts/save           把当前编辑器内容存盘
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

from pydantic import BaseModel

from gui.web.ws import ws_manager

log = logging.getLogger(__name__)

# 复用 Phase 5 的凭证 helper
from gui.web.api.synthesize import _load_minimax_key, _load_volcengine_creds

# ============================================================
# 脚本存档目录（与 PySide6 GUI 共用）
# ============================================================
SCRIPT_DIR = Path.home() / "Library" / "Application Support" / "PodcastGenerator" / "scripts"
SCRIPT_DIR.mkdir(parents=True, exist_ok=True)


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
# 内部：把 topic / context / custom → chat messages（不复用 MiniMax 的私有函数）
# ============================================================
def _build_messages_for_topic(
    topic: str, context: str, custom: str, n_speakers: int, show_name: str,
) -> List[Dict[str, str]]:
    """直接调 minimax_script_gen.build_messages，复用它的 prompt 模板。"""
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
    context: str = ""            # 原始素材
    custom: str = ""             # 附加要求
    n_speakers: int = 2          # 2 或 3（仅 minimax；豆包硬 2-host 忽略）
    api_key: str = ""            # 留空 → fallback 到 config.json（仅 minimax）
    show_name: str = "医学科普播客"
    # === Phase 6+ 引擎选择 ===
    engine: str = "minimax"      # "minimax" | "doubao"
    appid: str = ""              # 豆包用；留空 → config.json / env
    token: str = ""              # 豆包用；留空 → config.json / env


class ScriptGenResponse(BaseModel):
    script_text: str
    segments: List[Dict[str, str]]
    count: int
    elapsed_sec: float
    saved_to: str = ""


def _format_for_save(
    segments: List[Dict[str, str]],
    topic: str,
    editor_text: str = "",
) -> dict:
    """存盘到 ~/Library/Application Support/PodcastGenerator/scripts/，文件名带时间戳。"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    short = (topic or "未命名主题")[:30].replace("/", "_").replace(" ", "_")
    name = f"script_{ts}_{short}.json"
    p = SCRIPT_DIR / name
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "topic": topic,
        "segments": segments,
        "editor_text": editor_text or segments_to_editor_text(segments),
    }
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(p)


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
    """
    引擎分发器。FastAPI 后台线程调起。
    - engine == "minimax" → MiniMax M3 chat completion
    - engine == "doubao"  → 豆包 PodcastTTS preview 模式（只要台词，不出音频）

    通过 ws_manager 推 log + progress + script_done/error。
    """
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
    """原 MiniMax M3 chat completion 流程。"""
    # 1. api_key fallback
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

        # thinking 模型可能要 1-3 分钟
        ws_manager.push({
            "type": "log", "level": "info",
            "message": "⏳ MiniMax M3 thinking 中（最长 4 分钟）…",
        })

        # 拿到 LLM 原始文本（不经过 minimax_script_gen 的 parse_script），
        # 因为它的 parse 在多 marker / `---` 块时只取第一个 marker，会丢一半段。
        messages = _build_messages_for_topic(
            topic, context, custom, n_speakers, show_name,
        )
        raw_text = gen._post(messages, max_tokens=None)

        # 用我们自己的 parse（更鲁棒，支持多 marker / `---` 块）重新切段
        from gui.web.api.parse import parse_script_lines
        try:
            segments = parse_script_lines(raw_text)
        except ValueError as e:
            # LLM 输出格式完全坏了 — 把原文回给用户看
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

        # 用我们 parse 后的 segments 重新生成 editor_text，确保一致
        script_text = segments_to_editor_text(segments)
        saved_to = _format_for_save(segments, topic, script_text)
        dt = time.time() - t0
        ws_manager.push({
            "type": "log", "level": "success",
            "message": f"✅ 生成 {len(segments)} 段，耗时 {dt:.1f}s，已存 {Path(saved_to).name}",
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
    """
    把豆包返回 [{speaker: voice_id, text, round_id}, ...] 映射成
    [{speaker: "主播甲" / "主播乙", text}, ...]。

    规则：
      1. 过滤 head_music（round_id=-1）和 tail_music（round_id=9999）
      2. 按出场顺序找 unique speaker → 第 1 个 = 主播甲，第 2 个 = 主播乙
      3. 第 3+ 个 speaker 静默丢弃（豆包硬 2-host）
    """
    # 1. 过滤伪 speaker
    filtered = [
        t for t in texts
        if t.get("round_id") not in (-1, 9999)
        and t.get("speaker") not in ("head_music", "tail_music")
        and (t.get("text") or "").strip()
    ]
    # 2. 找 unique speaker（保序）
    seen = []
    for t in filtered:
        spk = t.get("speaker", "")
        if spk and spk not in seen:
            seen.append(spk)
    name_map = {}
    for i, spk in enumerate(seen[:2]):  # 最多 2 个
        name_map[spk] = "主播甲" if i == 0 else "主播乙"

    # 3. 映射
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
    show_name: str,  # 豆包不接 show_name，留作日志展示
) -> Optional[ScriptGenResponse]:
    """豆包 PodcastTTS：preview 模式，只要 text，不出音频。"""
    # 1. 凭证 fallback
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
        # 拼 input_text：topic + 可选 context + 可选 custom
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

        # 跑豆包（async → sync；用 /tmp 兜个 output_path 因为 only_nlp_text 模式不会写）
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
            # 已有 event loop 的情况（Jupyter/嵌套），退到 run_coroutine_threadsafe 不可行
            # 这里简单回退：在线程里跑新 loop
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

        # 解析成 segments
        texts = result.get("texts", []) or []
        segments = _doubao_texts_to_segments(texts)
        if not segments:
            raise RuntimeError(
                f"豆包返回 0 段（raw texts={len(texts)}，可能被 head/tail music 全过滤）"
            )

        script_text = segments_to_editor_text(segments)
        saved_to = _format_for_save(segments, topic, script_text)
        dt = time.time() - t0
        ws_manager.push({
            "type": "log", "level": "success",
            "message": f"✅ 豆包生成 {len(segments)} 段，耗时 {dt:.1f}s，已存 {Path(saved_to).name}",
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
# /api/scripts/recent
# ============================================================
class ScriptSummary(BaseModel):
    name: str
    path: str
    created_at: str = ""
    topic: str = ""
    n_segments: int = 0
    preview: str = ""        # 前 80 字


def list_recent_scripts(limit: int = 20) -> List[ScriptSummary]:
    if not SCRIPT_DIR.exists():
        return []
    files = sorted(
        SCRIPT_DIR.glob("script_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:limit]
    out = []
    for p in files:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        segs = d.get("segments", [])
        preview = ""
        if segs:
            first_text = segs[0].get("text", "")
            preview = first_text[:80] + ("..." if len(first_text) > 80 else "")
        out.append(ScriptSummary(
            name=p.name,
            path=str(p),
            created_at=d.get("created_at", ""),
            topic=d.get("topic", ""),
            n_segments=len(segs),
            preview=preview,
        ))
    return out


# ============================================================
# /api/scripts/save
# ============================================================
class ScriptSaveRequest(BaseModel):
    script_text: str
    topic: str = ""


def save_script_impl(script_text: str, topic: str = "") -> dict:
    """把当前编辑器内容存盘。返回 {saved_to, name}。"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    short = (topic or "手动保存")[:30].replace("/", "_").replace(" ", "_")
    name = f"script_{ts}_{short}.json"
    p = SCRIPT_DIR / name
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "topic": topic or "手动保存",
        "editor_text": script_text,
        "source": "manual",
    }
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"saved_to": str(p), "name": name}
