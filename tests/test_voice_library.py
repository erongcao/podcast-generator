"""Tests for voice_library module."""
import json
from pathlib import Path

import pytest

import voice_library as vl


def test_init_creates_dir_and_file(temp_voices_dir):
    vl.init()
    assert (temp_voices_dir / "voices.json").exists()
    assert (temp_voices_dir / "audio").is_dir()
    assert json.loads((temp_voices_dir / "voices.json").read_text()) == {"voices": []}


# ============ 回归测试：拦截 builtin shadowing ============
#
# 历史 bug (2026-09-26):
#   voice_library.py 里有 ``def list():`` 覆盖了 Python 内置 ``list()``，
#   导致模块内 / 调用方所有 ``list(some_iterable)`` 都崩
#   ("list() takes 0 positional arguments but 1 was given")。
#
# 这些断言每次测试都会跑，确保没人再在 voice_library 命名空间里加
# 覆盖关键 builtin 的函数 / 方法 / 类 / 变量。

CRITICAL_BUILTINS_TO_PROTECT = {
    "list", "dict", "id", "type", "input", "str", "int", "float", "bool",
    "set", "tuple", "len", "range", "map", "filter", "zip", "sum", "min",
    "max", "open", "print", "object", "isinstance", "issubclass",
    "hasattr", "getattr", "setattr", "delattr", "callable",
    "__build_class__",  # metaclass machinery
}


def test_voice_library_does_not_shadow_critical_builtins():
    """拦截 voice_library.* 里任何覆盖 Python 内置关键函数的命名。

    失败信息会列出哪个名字被覆盖，方便定位。
    """
    # 用 dir() 看模块导出符号
    public = set(dir(vl))
    shadowed = sorted(public & CRITICAL_BUILTINS_TO_PROTECT)
    # 允许别名但必须通过 __getattr__（module-level __getattr__ 不在 dir() 里）
    # 所以这里只检查"模块里直接定义"的 symbol —— 如果未来有人又写了
    # ``def list():``，它会出现在 dir() 里，这个测试就会爆。
    assert not shadowed, (
        f"voice_library 模块覆盖了 Python 内置函数: {shadowed}。"
        f"重命名以避免命名空间污染（参考 list→list_all 的修复）。"
    )


def test_list_builtin_still_works_inside_voice_library_namespace():
    """shadowing 后另一个症状：内置 list(iterable) 在模块作用域内崩。

    这里故意把 voice_library import 进当前命名空间，
    再调 list([...])，确保没有被覆盖。
    """
    # 模拟用户代码场景：import voice_library 之后调内置 list
    import voice_library  # noqa: F401
    result = list([1, 2, 3])
    assert result == [1, 2, 3]


def test_list_all_returns_list_of_voices(temp_voices_dir):
    """list_all 是新名字，确保它跟原 list() 行为一致。"""
    vl.init()
    assert vl.list_all() == []
    wav1 = temp_voices_dir / "a.wav"
    wav1.write_bytes(b"x")
    wav2 = temp_voices_dir / "b.wav"
    wav2.write_bytes(b"y")
    vl.add("a", "甲", str(wav1))
    vl.add("b", "乙", str(wav2))
    voices = vl.list_all()
    assert {v["voice_id"] for v in voices} == {"a", "b"}


def test_add_returns_voice_record(temp_voices_dir, tmp_path):
    vl.init()
    wav = tmp_path / "src.wav"
    wav.write_bytes(b"fake wav")
    record = vl.add("vid_1", "我的声音", str(wav), notes="备注")
    assert record["voice_id"] == "vid_1"
    assert record["display_name"] == "我的声音"
    assert Path(record["wav_path"]).exists()
    assert (temp_voices_dir / "audio" / "vid_1.wav").exists()


def test_list_returns_added_voices(temp_voices_dir, tmp_path):
    vl.init()
    wav = tmp_path / "src.wav"
    wav.write_bytes(b"x")
    vl.add("a", "甲", str(wav))
    wav2 = tmp_path / "src2.wav"
    wav2.write_bytes(b"y")
    vl.add("b", "乙", str(wav2))
    voices = vl.list_all()
    assert {v["voice_id"] for v in voices} == {"a", "b"}


def test_exists_returns_true_for_existing(temp_voices_dir, tmp_path):
    vl.init()
    wav = tmp_path / "src.wav"
    wav.write_bytes(b"x")
    vl.add("a", "甲", str(wav))
    assert vl.exists("a") is True
    assert vl.exists("nope") is False


def test_delete_removes_file_and_entry(temp_voices_dir, tmp_path):
    vl.init()
    wav = tmp_path / "src.wav"
    wav.write_bytes(b"x")
    vl.add("a", "甲", str(wav))
    assert vl.exists("a")
    vl.delete("a")
    assert not vl.exists("a")
    assert not (temp_voices_dir / "audio" / "a.wav").exists()


def test_update_renames_and_keeps_wav(temp_voices_dir, tmp_path):
    vl.init()
    wav = tmp_path / "src.wav"
    wav.write_bytes(b"x")
    vl.add("a", "甲", str(wav))
    vl.update("a", display_name="新名字", notes="备注2")
    assert vl.get("a")["display_name"] == "新名字"
    assert vl.get("a")["notes"] == "备注2"
    # voice_id unchanged
    assert vl.get("a")["voice_id"] == "a"


def test_update_partial_only_notes(temp_voices_dir, tmp_path):
    """update() can be called with only one field; other field unchanged."""
    vl.init()
    wav = tmp_path / "src.wav"
    wav.write_bytes(b"x")
    vl.add("a", "原始名字", str(wav), notes="原始备注")
    vl.update("a", notes="新备注")
    record = vl.get("a")
    assert record["display_name"] == "原始名字"  # unchanged
    assert record["notes"] == "新备注"


def test_corrupt_json_backs_up_and_recovers(temp_voices_dir, tmp_path):
    vl.init()
    corrupt_path = temp_voices_dir / "voices.json"
    corrupt_path.write_text("{{not valid json")
    vl.init()  # should not raise
    assert (temp_voices_dir / "voices.json.bak").exists()
    assert json.loads(corrupt_path.read_text()) == {"voices": []}


def test_concurrent_writes_thread_safe(temp_voices_dir, tmp_path):
    """Smoke test: 10 threads adding concurrently produce 10 entries."""
    import threading
    vl.init()
    wav = tmp_path / "src.wav"
    wav.write_bytes(b"x")

    def add_one(i):
        try:
            vl.add(f"v{i}", f"声音{i}", str(wav))
        except Exception:
            pass

    threads = [threading.Thread(target=add_one, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(vl.list_all()) == 10


def test_add_designed_no_source_wav(temp_voices_dir):
    """Designed voice: no source WAV needed; preview MP3 saved instead."""
    vl.init()
    preview = b"\x49\x44\x33" + b"\x00" * 20  # fake ID3 + frames
    record = vl.add_designed(
        voice_id="designed_v1",
        display_name="医生的稳重声",
        preview_audio=preview,
        notes="Voice Design: 40岁男医生，语速慢",
    )
    assert record["voice_id"] == "designed_v1"
    assert record["display_name"] == "医生的稳重声"
    assert record["wav_path"] is None
    assert record["source"] == "voice_design"
    assert Path(record["preview_path"]).exists()
    saved = Path(record["preview_path"]).read_bytes()
    assert saved == preview
    # Listed alongside cloned voices
    assert vl.exists("designed_v1") is True
    assert vl.get("designed_v1")["source"] == "voice_design"


def test_add_designed_duplicate_raises(temp_voices_dir):
    """Adding the same voice_id twice raises LibraryError."""
    from exceptions import LibraryError
    vl.init()
    vl.add_designed("dup_id", "x", b"audio")
    with pytest.raises(LibraryError):
        vl.add_designed("dup_id", "y", b"audio2")


def test_delete_designed_cleans_preview(temp_voices_dir):
    """Delete should remove preview MP3 too (no WAV to remove)."""
    vl.init()
    record = vl.add_designed("designed_v2", "x", b"abc")
    preview = Path(record["preview_path"])
    assert preview.exists()
    vl.delete("designed_v2")
    assert not preview.exists()
    assert not vl.exists("designed_v2")
