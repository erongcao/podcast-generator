"""
editor.py — /editor 视图后端：preview 渲染 + script export

Phase 1 仅含 export（Task 1）；preview 留到后续 task。

API：
    GET /api/scripts/export?name=<>&format=<raw|rendered>

设计要点：
    - export_impl 与 route 同文件（不跨模块跳）
    - 安全校验 byte-for-byte 复用 load_script_impl 的策略
    - rendered 优先用 segments（DB-authoritative），fallback 到 parse_script_lines
      解析 editor_text（兼容旧脚本只有 editor_text 的情况）
    - 文件名 = sanitize_filename(topic) + ".md"
"""
from __future__ import annotations

import json
import re
from typing import List, Dict, Tuple

from gui.web.api.script_gen import SCRIPT_DIR
from gui.web.api.parse import parse_script_lines

# ============================================================
# sanitize_filename
# ============================================================
_UNSAFE_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str) -> str:
    """把文件名里的 unsafe 字符替换成 `_`；空 / 纯空白 → "untitled_script"。

    中文 / 非 ASCII 保留原样（route 层会用 RFC 5987 percent-encode 进 header）。
    """
    if not name or not name.strip():
        return "untitled_script"
    safe = _UNSAFE_FILENAME_CHARS.sub("_", name)
    return safe.strip(" ._") or "untitled_script"


# ============================================================
# render_markdown
# ============================================================
def render_markdown(segments: List[Dict[str, str]]) -> str:
    """
    把 segments → markdown 字符串。格式：

        ---

        ## 主播甲

        body text

        ---

        ## 主播乙

        body text

    空列表返回 ""。
    """
    if not segments:
        return ""
    parts = []
    for s in segments:
        text = (s.get("text") or "").strip()
        speaker = s.get("speaker", "").strip()
        if not text or not speaker:
            continue
        parts.append(f"---\n\n## {speaker}\n\n{text}\n")
    if not parts:
        return ""
    # 去掉最后一段的多余换行符
    return "\n".join(parts).rstrip() + "\n"


# ============================================================
# export_impl
# ============================================================
def export_impl(name: str, format: str) -> Tuple[str, str]:
    """
    读 ~/Library/Application Support/PodcastGenerator/scripts/{name}.json，
    导出为 markdown，返回 (markdown_text, download_filename)。

    Args:
        name: 文件名（必须 script_*.json，不能含路径分隔符 / `..`）
        format: "raw" → 原样返回 editor_text；
                "rendered" → 渲染 markdown（优先 segments，fallback 到 parse_script_lines）

    Returns:
        (markdown_text, download_filename) 其中 download_filename = "{sanitized_topic}.md"

    Raises:
        ValueError: name 非法 或 format 非法
        FileNotFoundError: 文件不存在
    """
    # 1. name 安全校验（与 load_script_impl 一致 — byte-for-byte 复制策略）
    if not name or not name.startswith("script_") or not name.endswith(".json"):
        raise ValueError(f"非法文件名: {name!r}")
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError(f"文件名含路径分隔符: {name!r}")

    # 2. format 校验
    if format not in ("raw", "rendered"):
        raise ValueError(f"非法 format: {format!r}（仅接受 'raw' 或 'rendered'）")

    p = SCRIPT_DIR / name
    if not p.exists():
        raise FileNotFoundError(name)

    data = json.loads(p.read_text(encoding="utf-8"))
    topic = data.get("topic", "") or ""

    # 3. 按 format 返回内容
    if format == "raw":
        md = data.get("editor_text", "") or ""
    else:  # "rendered"
        segments = data.get("segments", []) or []
        if segments:
            md = render_markdown(segments)
        else:
            # fallback: 旧脚本只有 editor_text 时，解析它
            editor_text = data.get("editor_text", "") or ""
            if not editor_text.strip():
                md = ""
            else:
                try:
                    fallback_segs = parse_script_lines(editor_text)
                except ValueError:
                    # 旧脚本 editor_text 完全损坏 → 退化为原文
                    md = editor_text
                else:
                    md = render_markdown(fallback_segs)

    filename = f"{sanitize_filename(topic)}.md"
    return md, filename
