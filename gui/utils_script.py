#!/usr/bin/env python3
"""
脚本格式检测工具（独立小工具，与具体 UI 解耦）

判定一段文本是否符合「【角色】\\n台词正文」格式，用于文件导入时的快捷识别。
"""
import re

# 【角色名】开头 + 至少 1 轮（多行）
SCRIPT_FORMAT_RE = re.compile(r"【[^\[\]\n]{1,30}】\s*\n", re.MULTILINE)


def is_script_format(text: str) -> bool:
    """启发式判断文本是否已经是脚本格式（≥2 轮）。"""
    if not text:
        return False
    matches = SCRIPT_FORMAT_RE.findall(text)
    return len(matches) >= 2
