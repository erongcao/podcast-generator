"""
parse.py — 脚本解析器（Web 后端版）

支持用户**真实使用的格式**：
    ---

    【主播甲】
    正文第一行
    正文第二行

    ---

    【主播乙】
    正文...

也兼容 PySide6 时代的老格式（向后兼容，不破坏现有用户脚本）：
    [主播甲]: 正文
    主播甲：正文
    甲: 正文
    A: 正文

**Why this file exists separately from `gui/qt/panels/output.py`**：
旧版 `output.py:_SPEAKER_PATTERN` 三个条件全错（半角 [] 而非【】、要求冒号、
要求单行），导致用户真实脚本每次 parse 返回 [] 走 fallback，整段被打包成
"主播甲"独白送进 TTS。Phase 2 重写。

**失败策略**：
parse 失败（既无 marker 又无任何 standalone marker）**必须 raise**。
绝不允许"静默 fallback 到主播甲独白"——用户已受够这种"程序以为自己工作了"
的假阳性（见 [[podcast-voxcpm-1seg-ux-bug]] 反思）。
"""
from __future__ import annotations

import re
from typing import List, Dict

# marker 行的可选 speaker 列表（顺序很重要：长的先匹配）
SPEAKER_TOKENS = (
    r"主播[甲乙丙丁戊己庚辛壬癸]"      # 主播甲..主播癸
    r"|主播[1-9]"                     # 主播1..主播9
    r"|[甲乙丙丁戊己庚辛壬癸]"         # 单字 甲/乙/...
    r"|[A-E]"                          # A/B/C/D/E
    r"|[1-9]"                          # 1..9
    r"|男声|女声"                       # 男声/女声
    r"|旁白"                            # 旁白
    r"|男|女"                           # 男/女
)

# 匹配 marker 行（独立成行，可选内联 body）：
#   【主播甲】        ← 独立 marker，正文走后续行
#   [主播甲]: 你好    ← 老格式带冒号和内联 body
#   主播乙：你好     ← 旧格式全角冒号
#   A: hi            ← 短标签
_MARKER_PATTERN = re.compile(
    r"^\s*[\[【]?(" + SPEAKER_TOKENS + r")[\]】]?"
    r"(?:\s*[::：]\s*(.*))?$"
)

# 匹配独立成行的 `---` 分隔符（3 个或更多连字符，前后允许空格）
_SEPARATOR_PATTERN = re.compile(r"^\s*-{3,}\s*$")


def _is_separator(line: str) -> bool:
    """`---` 或 `--------` 这种纯连字符行 = 段间分隔符。"""
    return bool(_SEPARATOR_PATTERN.match(line))


def parse_script_lines(text: str) -> List[Dict[str, str]]:
    """
    把脚本文本切成 [{speaker, text}, ...] 段列表。

    Args:
        text: 原始脚本（用户从编辑器粘贴或文件读取）

    Returns:
        段列表。每段 = {"speaker": "主播甲", "text": "正文..."}

    Raises:
        ValueError: 解析失败（既无 marker 又无内容）。
                    调用方**应该**让前端看到错误，而不是静默 fallback。
    """
    if not text or not text.strip():
        raise ValueError("脚本为空")

    lines = text.splitlines()
    segments: List[Dict[str, str]] = []
    current_speaker: str | None = None
    current_body: List[str] = []

    def flush():
        """把当前 speaker + body 推入 segments。"""
        nonlocal current_speaker, current_body
        if current_speaker and current_body:
            body = "\n".join(current_body).strip()
            if body:
                segments.append({"speaker": current_speaker, "text": body})
        current_speaker = None
        current_body = []

    for raw in lines:
        line = raw.rstrip()

        # 1) 空行：保留作为 body 内部空行（拼接时仍会被 strip 掉）
        if not line.strip():
            if current_speaker:
                current_body.append("")
            continue

        # 2) `---` 分隔符：flush 当前段
        if _is_separator(line):
            flush()
            continue

        # 3) 尝试匹配 marker
        m = _MARKER_PATTERN.match(line)
        if m:
            # 先 flush 上一段
            flush()
            current_speaker = m.group(1)
            inline_body = (m.group(2) or "").strip()
            if inline_body:
                # 老格式：marker + 冒号 + 内联 body，全在一行
                current_body = [inline_body]
                # 这里不要立即 flush，下一行如果是 `---` 或下一个 marker 才 flush
            else:
                # 新格式：marker 独立成行，body 走后续行
                current_body = []
            continue

        # 4) 普通文本行：追加到当前 body
        if current_speaker is not None:
            current_body.append(line)
        else:
            # 没有 speaker 但有文本：可能是用户在 marker 之前写了前言
            # → 暂存，碰到第一个 marker 时丢弃（或作为"主播甲"前言）
            # 简化：直接 raise，让用户修正
            raise ValueError(
                f"脚本开头有未标记的文本: {line[:50]!r}..."
                f"\n请在开头加 marker（如 【主播甲】）或删除这段文字。"
            )

    # 收尾：flush 最后一段
    flush()

    if not segments:
        raise ValueError(
            "脚本解析失败：没找到任何 marker。\n"
            "支持的格式：\n"
            "  ---\n"
            "  【主播甲】\n"
            "  正文...\n"
            "  ---\n"
            "  【主播乙】\n"
            "  正文...\n"
            "\n"
            "或老格式：\n"
            "  [主播甲]: 正文\n"
            "  [主播乙]: 正文"
        )

    return segments
