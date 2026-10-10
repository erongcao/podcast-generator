"""Tests for parse_script_segments (gui/panels/output.py).

Regression: 2026-09-26 用户编辑台词时在段首夹带了 ``\\n\\n--\\n\\n`` 残留，
原解析器只看 seg[1:brace_end]，会把 ``--\\n\\n【主播甲`` 整个当成 speaker，
导致 voice_map 查不到 "主播甲" → "VoxCPM2 合成失败: 段 0 没找到对应 voice"。
修复后用 find("【") + find("】") 锁定第一对括号，段首脏字符忽略。
"""
import pytest

from gui.panels.output import parse_script_segments


def test_basic_two_speakers():
    text = "【主播甲】\n你好\n\n---\n\n【主播乙】\n世界"
    out = parse_script_segments(text)
    assert len(out) == 2
    assert out[0]["speaker"] == "主播甲"
    assert out[0]["text"] == "你好"
    assert out[1]["speaker"] == "主播乙"
    assert out[1]["text"] == "世界"


def test_garbage_prefix_is_ignored():
    """用户编辑时常见段首夹带 '\\n\\n--\\n\\n' 残留。"""
    text = (
        "【主播甲】\n你好\n\n---\n\n"
        "--\n\n【主播乙】\n世界\n\n---\n\n"
        "\n\n【主播丙】\n晚安"
    )
    out = parse_script_segments(text)
    assert len(out) == 3, f"应解析 3 段，实际 {len(out)}: {out}"
    speakers = [s["speaker"] for s in out]
    assert speakers == ["主播甲", "主播乙", "主播丙"], speakers
    assert out[1]["text"] == "世界"


def test_extra_whitespace_stripped():
    text = "  \n  【主播甲】  \n  文本  \n  \n\n---\n\n  【主播乙】\n  内容  "
    out = parse_script_segments(text)
    assert out[0]["speaker"] == "主播甲"
    assert out[0]["text"] == "文本"
    assert out[1]["speaker"] == "主播乙"
    assert out[1]["text"] == "内容"


def test_segment_without_brace_is_skipped():
    text = "这段没有括号\n\n---\n\n【主播甲】\n你好"
    out = parse_script_segments(text)
    assert len(out) == 1
    assert out[0]["speaker"] == "主播甲"


def test_empty_speaker_skipped():
    """【】是空 speaker（光标没输入），跳过。"""
    text = "【】\n空文本\n\n---\n\n【主播甲】\n你好"
    out = parse_script_segments(text)
    assert len(out) == 1
    assert out[0]["speaker"] == "主播甲"


def test_only_one_brace_matched_skipped():
    """只有一个【 没有 】的段跳过（不算合法）。"""
    text = "【未闭合\n\n---\n\n【主播甲】\n好"
    out = parse_script_segments(text)
    assert len(out) == 1
    assert out[0]["speaker"] == "主播甲"


def test_only_garbage_returns_empty():
    text = "\n\n---\n\n\n\n---"
    out = parse_script_segments(text)
    assert out == []


def test_voice_map_lookup_after_garbage_prefix():
    """核心回归：parser 修复后，voice_map.get(speaker) 应该命中。"""
    text = (
        "【主播甲】\n第一句\n\n---\n\n"
        "--\n\n【主播乙】\n第二句"
    )
    voice_map = {"主播甲": "jiangyu-4", "主播乙": "talarico-james"}
    out = parse_script_segments(text)
    for seg in out:
        vid = voice_map.get(seg["speaker"])
        assert vid, f"speaker_map 未命中: {seg['speaker']!r}"


def test_multiline_text_preserved():
    """text 字段保留段内换行（user 写了多段台词时）。"""
    text = "【主播甲】\n第一行\n第二行\n第三行"
    out = parse_script_segments(text)
    assert len(out) == 1
    assert "第一行" in out[0]["text"]
    assert "第二行" in out[0]["text"]
    assert "第三行" in out[0]["text"]