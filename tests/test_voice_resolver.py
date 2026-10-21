"""
voice_resolver 回归测试（2026-10-21）

背景：v0.4.0 把音色存储搬到 SQLite（`voice_clones`），但 TTS 引擎侧仍然只查遗留
JSON（`voice_library.get()`）。结果从看板录音的音色在合成时完全不存在：

    User 在看板录音 → add_voice_impl 写进 SQLite voice_clones
    → voxcpm_tts 查 voice_library.get("yujialu") → None
    → VoiceCloneError: 音色库中不存在 voice_id=yujialu

这里锁住"两个存储都能查到"这条不变量。
"""
import json
from pathlib import Path

import pytest


@pytest.fixture
def legacy_json(tmp_path, monkeypatch):
    """一个只含遗留 JSON 的环境（DB 查询会失败并静默退回）。"""
    monkeypatch.setattr("voice_library.VOICES_DIR", tmp_path)
    monkeypatch.setattr("voice_library.VOICES_JSON", tmp_path / "voices.json")
    (tmp_path / "voices.json").write_text(
        json.dumps({"voices": [{
            "voice_id": "json-only",
            "display_name": "JSON 独有",
            "wav_path": str(tmp_path / "json-only.wav"),
            "ref_text": "参考文本",
            "tags": ["female"],
            "minimax_voice_id": "json-clone-001",
        }]}, ensure_ascii=False),
        encoding="utf-8",
    )
    return tmp_path


def test_finds_voice_in_legacy_json(legacy_json):
    import voice_resolver

    rec = voice_resolver.resolve("json-only")
    assert rec is not None, "遗留 JSON 里的音色必须还能查到"
    assert rec["display_name"] == "JSON 独有"
    assert rec["minimax_voice_id"] == "json-clone-001"


def test_missing_voice_returns_none(legacy_json):
    import voice_resolver

    assert voice_resolver.resolve("no-such-voice") is None
    assert voice_resolver.resolve("") is None


def test_resolve_wav_prefers_cleaned_version(legacy_json):
    """cleaned.wav 与原 wav 同 stem 时优先用清洗版（48kHz / -16 LUFS）。"""
    import voice_resolver

    raw = legacy_json / "json-only.wav"
    raw.write_bytes(b"fake")
    cleaned = legacy_json / "json-only.cleaned.wav"
    cleaned.write_bytes(b"fake")

    wav_path, ref_text = voice_resolver.resolve_wav("json-only")
    assert wav_path == str(cleaned)
    assert ref_text == "参考文本"


def test_resolve_wav_falls_back_to_raw(legacy_json):
    """没有 cleaned.wav 时用原 wav。"""
    import voice_resolver

    raw = legacy_json / "json-only.wav"
    raw.write_bytes(b"fake")

    wav_path, _ = voice_resolver.resolve_wav("json-only")
    assert wav_path == str(raw)


def test_resolve_wav_raises_for_unknown_voice(legacy_json):
    from exceptions import VoiceCloneError
    import voice_resolver

    with pytest.raises(VoiceCloneError, match="音色库中不存在"):
        voice_resolver.resolve_wav("nope")


def test_db_only_voice_is_visible_to_engines(monkeypatch):
    """
    核心回归：只在 SQLite 里、遗留 JSON 里没有的音色，引擎必须能查到。
    （模拟真实故障：看板录音 → 只进 voice_clones）
    """
    import voice_resolver

    class _Row:
        voice_id = "db-only"
        display_name = "小俞"
        ref_text = None
        notes = None
        tags = ["female"]
        audio_path = "/tmp/db-only.wav"

    class _Q:
        def filter(self, *a, **k): return self
        def first(self): return _Row()

    class _DB:
        def query(self, *a, **k): return _Q()
        def close(self): pass

    monkeypatch.setattr("gui.web.db.SessionLocal", lambda: _DB(), raising=False)
    monkeypatch.setattr("gui.web.auth.get_default_owner",
                        lambda db: type("O", (), {"id": 1})(), raising=False)

    # 让遗留 JSON 查询明确返回 None，模拟"JSON 里没有这条"
    monkeypatch.setattr(voice_resolver, "_from_legacy_json", lambda vid: None)

    rec = voice_resolver.resolve("db-only")
    assert rec is not None, "只存在于 SQLite 的音色必须能被引擎查到"
    assert rec["wav_path"] == "/tmp/db-only.wav"
    assert rec["display_name"] == "小俞"
    assert rec["tags"] == ["female"]
    # 还没克隆过 → 没有服务端 ID。不能拿本地 ID 硬凑，
    # 否则 MiniMax 会回 voice id not exist。
    assert rec["minimax_voice_id"] == ""
    assert voice_resolver.resolve_minimax_voice_id("db-only") is None


def test_db_voice_with_clone_uses_server_id(monkeypatch):
    """已克隆过的 DB 音色必须返回真正的服务端 ID（不是本地 ID）。"""
    import voice_resolver

    class _Row:
        voice_id = "cloned"
        display_name = "已克隆"
        ref_text = None
        notes = None
        tags = []
        audio_path = "/tmp/cloned.wav"
        minimax_voice_id = "cloned-server-001"

    class _Q:
        def filter(self, *a, **k): return self
        def first(self): return _Row()

    class _DB:
        def query(self, *a, **k): return _Q()
        def close(self): pass

    monkeypatch.setattr("gui.web.db.SessionLocal", lambda: _DB(), raising=False)
    monkeypatch.setattr("gui.web.auth.get_default_owner",
                        lambda db: type("O", (), {"id": 1})(), raising=False)
    monkeypatch.setattr(voice_resolver, "_from_legacy_json", lambda vid: None)

    assert voice_resolver.resolve_minimax_voice_id("cloned") == "cloned-server-001"


def test_db_unavailable_falls_back_to_json_only(legacy_json, monkeypatch):
    """数据库炸了（CLI 环境常见）不能让 JSON 里的音色也查不到。"""
    import voice_resolver

    def _boom(*a, **k):
        raise RuntimeError("no database here")

    monkeypatch.setattr("gui.web.db.SessionLocal", _boom, raising=False)

    assert voice_resolver.resolve("json-only") is not None
    assert voice_resolver.resolve_minimax_voice_id("json-only") == "json-clone-001"


def test_voxcpm_resolve_wav_uses_resolver(legacy_json):
    """voxcpm_tts._resolve_wav 必须走 resolver，否则看板音色又会消失。"""
    import voice_resolver
    import voxcpm_tts

    raw = legacy_json / "json-only.wav"
    raw.write_bytes(b"fake")

    wav_path, ref_text = voxcpm_tts._resolve_wav("json-only")
    assert wav_path == str(raw)
    assert ref_text == "参考文本"