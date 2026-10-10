"""Tests for MiniMax subtitle support, multi-format, model/language helpers.

These tests cover the pure-Python helpers in `minimax_tts` that don't hit the
network. The live API path (`synthesize_with_subtitles`, `_synthesize_api`)
is exercised separately by hand / smoke probes — we mock it for full unit
isolation here.
"""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import minimax_tts
from minimax_tts import (
    _normalize_subtitle_items,
    _subtitle_json_to_srt,
    get_supported_emotions,
    get_supported_formats,
    get_supported_languages,
    get_supported_models,
)


# ============== SRT conversion ==============

def test_subtitle_json_to_srt_basic():
    items = [
        {"text": "你好", "start": 0.0, "end": 1.5},
        {"text": "世界", "start": 1.5, "end": 3.0},
    ]
    srt = _subtitle_json_to_srt(items)
    assert "1\n" in srt
    assert "00:00:00,000 --> 00:00:01,500" in srt
    assert "你好" in srt
    assert "2\n" in srt
    assert "00:00:01,500 --> 00:00:03,000" in srt
    assert "世界" in srt


def test_subtitle_json_to_srt_empty():
    assert _subtitle_json_to_srt([]) == ""


def test_subtitle_json_to_srt_minutes_and_seconds():
    items = [{"text": "测试", "start": 65.5, "end": 67.25}]
    srt = _subtitle_json_to_srt(items)
    # 65.5s = 00:01:05,500 ; 67.25s = 00:01:07,250
    assert "00:01:05,500 --> 00:01:07,250" in srt
    assert "测试" in srt


def test_subtitle_json_to_srt_skips_blank_text():
    items = [
        {"text": "   ", "start": 0.0, "end": 1.0},  # blank
        {"text": "实际内容", "start": 1.0, "end": 2.0},
    ]
    srt = _subtitle_json_to_srt(items)
    # 序号应该跳过空白段
    assert "1\n" in srt
    assert "实际内容" in srt
    assert "2\n" not in srt  # 不应出现第二段


def test_subtitle_json_to_srt_zero_duration_guard():
    """end <= start 时应自动延后 0.5s 防止播放器拒绝。"""
    items = [{"text": "短", "start": 5.0, "end": 5.0}]
    srt = _subtitle_json_to_srt(items)
    # 5.0 → 5.5 即 00:00:05,000 → 00:00:05,500
    assert "00:00:05,000 --> 00:00:05,500" in srt


def test_subtitle_json_to_srt_negative_start_clamped():
    """start 为负应被夹到 0。"""
    items = [{"text": "x", "start": -1.0, "end": 1.0}]
    srt = _subtitle_json_to_srt(items)
    assert "00:00:00,000 --> 00:00:01,000" in srt


def test_subtitle_json_to_srt_srt_format():
    """SRT 格式：序号行 / 时间行 / 文本行 / 空行。"""
    items = [{"text": "你好", "start": 0.0, "end": 1.0}]
    srt = _subtitle_json_to_srt(items)
    lines = srt.split("\n")
    assert lines[0] == "1"
    assert lines[1] == "00:00:00,000 --> 00:00:01,000"
    assert lines[2] == "你好"
    assert lines[3] == ""  # 段间空行


# ============== Normalize from MiniMax raw payload ==============

def test_normalize_subtitle_items_sentence_level():
    """sentence 级别：time_begin/time_end 为 ms，需 /1000 转换。"""
    raw = [{
        "text": "你好世界。",
        "time_begin": 0.0,
        "time_end": 2500.0,  # ms
    }]
    out = _normalize_subtitle_items(raw)
    assert len(out) == 1
    assert out[0]["text"] == "你好世界。"
    assert out[0]["start"] == 0.0
    assert out[0]["end"] == 2.5


def test_normalize_subtitle_items_word_level():
    """word 级别：timestamped_words 子数组拆分更细粒度。"""
    raw = [{
        "text": "你好世界。",
        "time_begin": 0.0,
        "time_end": 2500.0,
        "timestamped_words": [
            {"word": "你", "time_begin": 0, "time_end": 200},
            {"word": "好", "time_begin": 200, "time_end": 400},
        ],
    }]
    out = _normalize_subtitle_items(raw)
    assert len(out) == 2
    assert out[0] == {"text": "你", "start": 0.0, "end": 0.2}
    assert out[1] == {"text": "好", "start": 0.2, "end": 0.4}


def test_normalize_subtitle_items_skips_blank():
    raw = [
        {"text": "  ", "time_begin": 0, "time_end": 1000},  # 空白跳过
        {"text": "实", "time_begin": 1000, "time_end": 2000},
    ]
    out = _normalize_subtitle_items(raw)
    assert len(out) == 1
    assert out[0]["text"] == "实"


def test_normalize_subtitle_items_uses_pronounce_word_fallback():
    """当 word 字段为空时回退到 pronounce_word。"""
    raw = [{
        "text": "x",
        "time_begin": 0,
        "time_end": 1000,
        "timestamped_words": [
            {"word": "", "pronounce_word": "嗯", "time_begin": 100, "time_end": 200},
        ],
    }]
    out = _normalize_subtitle_items(raw)
    assert out[0]["text"] == "嗯"


# ============== Capability lists ==============

def test_get_supported_models_includes_speech_28():
    models = get_supported_models()
    assert "speech-2.8-hd" in models
    assert "speech-2.8-turbo" in models
    assert models[0] == "speech-2.8-hd"  # 默认模型排首位


def test_get_supported_models_returns_fresh_list():
    """返回新 list 而不是共享可变全局，防止 UI 误改原列表。"""
    a = get_supported_models()
    b = get_supported_models()
    assert a is not b
    a.append("fake")
    assert "fake" not in get_supported_models()


def test_get_supported_formats_includes_common():
    fmts = get_supported_formats()
    for f in ("mp3", "pcm", "flac", "wav", "opus"):
        assert f in fmts


def test_get_supported_languages_includes_chinese_and_english():
    langs = get_supported_languages()
    assert "auto" in langs
    assert "Chinese" in langs
    assert "English" in langs


def test_get_supported_emotions_includes_calm():
    emos = get_supported_emotions()
    assert "calm" in emos
    assert "happy" in emos
    assert "whisper" in emos


def test_default_model_is_speech_28_hd():
    """保证默认模型与文档一致。"""
    assert minimax_tts.DEFAULT_MODEL == "speech-2.8-hd"


# ============== synthesize_with_subtitles (mocked) ==============

def test_synthesize_with_subtitles_returns_audio_and_srt(monkeypatch):
    """mock 掉网络请求，验证字幕返回结构正确。"""
    fake_audio_hex = "ffd8ffe0"  # 任意 hex
    fake_response = {
        "data": {
            "audio": fake_audio_hex,
            "status": 2,
            "subtitle_file": "https://oss.example.com/subtitles.json?Signature=xxx",
        },
        "base_resp": {"status_code": 0, "status_msg": "success"},
        "extra_info": {"audio_length": 1000, "audio_sample_rate": 32000},
    }
    fake_subtitle_payload = [
        {
            "text": "你好",
            "time_begin": 0.0,
            "time_end": 1500.0,
        }
    ]

    monkeypatch.setattr("minimax_tts._synthesize_api", lambda **kw: (b"\xff\xd8\xff\xe0", fake_response))
    monkeypatch.setattr("minimax_tts._fetch_subtitle_url", lambda url, key: fake_subtitle_payload)

    audio, srt, items = minimax_tts.synthesize_with_subtitles(
        text="你好",
        voice_id="male-qn-qingse",
        api_key="dummy-key",
        model="speech-2.8-turbo",
        subtitle_type="sentence",
    )

    assert audio == b"\xff\xd8\xff\xe0"
    assert len(items) == 1
    assert items[0]["text"] == "你好"
    assert items[0]["end"] == 1.5  # ms → s
    assert "你好" in srt
    assert "00:00:00,000 --> 00:00:01,500" in srt


def test_synthesize_with_subtitles_handles_missing_subtitle(monkeypatch):
    """subtitle_file 字段缺失时应返回空字幕但不抛错。"""
    fake_response = {
        "data": {"audio": "ffd8", "status": 2},  # 没有 subtitle_file
        "base_resp": {"status_code": 0, "status_msg": "success"},
    }
    monkeypatch.setattr("minimax_tts._synthesize_api", lambda **kw: (b"\xff\xd8", fake_response))

    audio, srt, items = minimax_tts.synthesize_with_subtitles(
        text="x", voice_id="v", api_key="k",
    )
    assert audio == b"\xff\xd8"
    assert srt == ""
    assert items == []


def test_synthesize_with_subtitles_handles_subtitle_fetch_failure(monkeypatch):
    """字幕 URL 拉取失败时音频应仍返回。"""
    fake_response = {
        "data": {"audio": "ffd8", "status": 2,
                 "subtitle_file": "https://oss.example.com/broken.json"},
        "base_resp": {"status_code": 0, "status_msg": "success"},
    }
    monkeypatch.setattr("minimax_tts._synthesize_api", lambda **kw: (b"\xff\xd8", fake_response))

    def _explode(url, key):
        raise RuntimeError("OSS unreachable")
    monkeypatch.setattr("minimax_tts._fetch_subtitle_url", _explode)

    audio, srt, items = minimax_tts.synthesize_with_subtitles(
        text="x", voice_id="v", api_key="k",
    )
    assert audio == b"\xff\xd8"
    assert srt == ""
    assert items == []


def test_synthesize_with_subtitles_passes_language_boost(monkeypatch):
    """language_boost 应被透传到 _synthesize_api。"""
    captured = {}
    def fake_synth(**kw):
        captured.update(kw)
        return (b"x", {"data": {}, "base_resp": {"status_code": 0, "status_msg": "success"}})

    monkeypatch.setattr("minimax_tts._synthesize_api", fake_synth)

    minimax_tts.synthesize_with_subtitles(
        text="hi", voice_id="v", api_key="k",
        language_boost="English",
    )
    assert captured["language_boost"] == "English"
    assert captured["subtitle_enable"] is True
    assert captured["subtitle_type"] == "sentence"


# ============== synthesize_segments 参数透传 ==============

def test_synthesize_segments_accepts_output_format_and_model(monkeypatch, tmp_path):
    """synthesize_segments 应透传 model 和 output_format 给底层 synthesize()。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "output").mkdir()

    captured = {}
    def fake_synthesize(**kw):
        captured.update(kw)
        return b"FAKE_AUDIO"
    monkeypatch.setattr("minimax_tts.synthesize", fake_synthesize)

    def fake_concat(out, files, output_format="mp3"):
        Path(out).write_bytes(b"COMBINED")
        return out
    monkeypatch.setattr("minimax_tts._concat_segments", fake_concat)

    segments = [{"speaker": "主播甲", "text": "hello"}]
    combined, order = minimax_tts.synthesize_segments(
        segments=segments,
        voice1_id="v1",
        voice2_id="v2",
        api_key="dummy",
        model="speech-2.8-turbo",
        output_format="wav",
        language_boost="English",
    )

    assert captured["model"] == "speech-2.8-turbo"
    assert captured["output_format"] == "wav"
    assert captured["language_boost"] == "English"
    assert "主播甲" in order


def test_synthesize_segments_uses_wav_extension_for_concat(monkeypatch, tmp_path):
    """output_format=wav 时，concat 应使用 wav 编码。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "output").mkdir()

    captured = {}
    def fake_concat(out, files, output_format="mp3"):
        captured["output_format"] = output_format
        captured["out"] = out
        Path(out).write_bytes(b"X")
    monkeypatch.setattr("minimax_tts._concat_segments", fake_concat)
    monkeypatch.setattr("minimax_tts.synthesize", lambda **kw: b"X")

    segments = [{"speaker": "主播甲", "text": "a"}, {"speaker": "主播乙", "text": "b"}]
    minimax_tts.synthesize_segments(
        segments=segments,
        voice1_id="v1", voice2_id="v2", api_key="k",
        output_format="wav",
    )
    assert captured["output_format"] == "wav"


# ============== _fetch_subtitle_url ==============

def test_fetch_subtitle_url_parses_list(monkeypatch):
    """mock requests.get：返回 list 即解析成功。"""
    import requests
    fake_resp = MagicMock()
    fake_resp.json.return_value = [{"text": "x", "time_begin": 0, "time_end": 1000}]
    fake_resp.raise_for_status = MagicMock()
    monkeypatch.setattr("minimax_tts.requests.get", lambda *a, **kw: fake_resp)

    items = minimax_tts._fetch_subtitle_url("https://oss.example.com/sub.json", "dummy")
    assert items == [{"text": "x", "time_begin": 0, "time_end": 1000}]


def test_fetch_subtitle_url_rejects_non_list(monkeypatch):
    """返回 dict（非 list）应抛 RuntimeError。"""
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"unexpected": "shape"}
    fake_resp.raise_for_status = MagicMock()
    monkeypatch.setattr("minimax_tts.requests.get", lambda *a, **kw: fake_resp)

    with pytest.raises(RuntimeError, match="Unexpected subtitle payload"):
        minimax_tts._fetch_subtitle_url("https://oss.example.com/sub.json", "dummy")