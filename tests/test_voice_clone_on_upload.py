"""
上传后自动克隆到 MiniMax 的回归测试（2026-10-21）

背景：`voice_clones` 表原本没有 `minimax_voice_id` 列，DB 里也没有任何"把音色
克隆到 MiniMax"的逻辑。结果看板新录的音色在 MiniMax 引擎上永远用不了 ——
`voice_resolver` 拿不到服务端 ID，就把本地 ID 原样发过去，换来
`voice id not exist`。

这里锁住三件事：
  1. 没配 API key → skipped，**不回滚音色**（ref 本地已经可用了）
  2. 克隆成功 → 写回 minimax_voice_id，且**幂等**（不重复消耗配额）
  3. 克隆失败 → failed + 错误信息留档，音色本身仍在
"""
import sys
import types
from pathlib import Path

import pytest


class _Row:
    """最小 VoiceClone 替身。"""

    def __init__(self, voice_id="test-v", audio_path=None):
        self.voice_id = voice_id
        self.display_name = voice_id
        self.ref_text = None
        self.notes = None
        self.tags = []
        self.created_at = None
        self.audio_path = audio_path
        self.minimax_voice_id = None
        self.clone_status = None
        self.clone_error = None


@pytest.fixture
def ref_wav(tmp_path):
    """真的落一个 ref 文件——_clone_to_minimax 会检查路径存在。"""
    p = tmp_path / "test-v.wav"
    p.write_bytes(b"RIFF....WAVEfmt ")
    return p


class _DB:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture
def fake_cloner(monkeypatch):
    """把 voice_cloner.clone 换成可配置的假实现。

    返回模块对象 mod（不是 fixture 函数自己），这样测试里
    ``fake_cloner.clone = ...`` / ``fake_cloner.should_fail = True``
    才是改到真正被 ``import voice_cloner`` 拿到的东西上。
    """
    mod = types.ModuleType("voice_cloner")

    def _clone(wav_path, voice_id, api_key, timeout=60):
        if mod.should_fail:
            from exceptions import VoiceCloneError
            raise VoiceCloneError("Clone API 错误: 配额已用尽")
        return voice_id

    mod.clone = _clone
    mod.should_fail = False
    monkeypatch.setitem(sys.modules, "voice_cloner", mod)
    return mod


def test_clone_success_records_server_id(fake_cloner, ref_wav):
    from gui.web.api.voices import _clone_to_minimax

    db, row = _DB(), _Row(audio_path=str(ref_wav))
    res = _clone_to_minimax(db, row, "sk-test")

    from gui.web.api.voices import _minimax_server_id

    assert res.clone_status == "done"
    expected = _minimax_server_id("test-v")
    assert res.minimax_voice_id == expected
    assert row.minimax_voice_id == expected
    assert row.clone_error is None


def test_clone_is_idempotent(fake_cloner, ref_wav):
    """已克隆过的直接返回 done，不再调 MiniMax（省配额）。"""
    from gui.web.api.voices import _clone_to_minimax

    db, row = _DB(), _Row(audio_path=str(ref_wav))
    row.minimax_voice_id = "already-done"

    calls = []
    fake_cloner.clone = lambda *a, **k: calls.append(a) or "x"

    res = _clone_to_minimax(db, row, "sk-test")
    assert res.clone_status == "done"
    assert res.minimax_voice_id == "already-done"
    assert not calls, "已经有服务端 ID 时不该再调 MiniMax"


def test_clone_without_api_key_is_skipped(fake_cloner, ref_wav):
    from gui.web.api.voices import _clone_to_minimax

    db, row = _DB(), _Row(audio_path=str(ref_wav))
    res = _clone_to_minimax(db, row, "")

    assert res.clone_status == "skipped"
    assert row.minimax_voice_id is None, "skipped 不该凭空造一个服务端 ID"
    assert row.clone_status == "skipped"


def test_clone_failure_keeps_voice_and_records_error(fake_cloner, ref_wav):
    """失败要留档，但音色本身照常可用（ref 在本地）。"""
    from gui.web.api.voices import _clone_to_minimax

    fake_cloner.should_fail = True
    db, row = _DB(), _Row(audio_path=str(ref_wav))

    res = _clone_to_minimax(db, row, "sk-test")

    assert res.clone_status == "failed"
    assert "配额已用尽" in res.clone_error
    assert row.clone_status == "failed"
    assert row.minimax_voice_id is None, "失败时不能留下一个假的 ID"


def test_clone_with_missing_ref_fails_cleanly(fake_cloner, tmp_path):
    from gui.web.api.voices import _clone_to_minimax

    db = _DB()
    row = _Row(audio_path=str(tmp_path / "nope.wav"))

    res = _clone_to_minimax(db, row, "sk-test")
    assert res.clone_status == "failed"
    assert "ref 音频不存在" in res.clone_error


def test_summary_exposes_clone_status(monkeypatch, tmp_path):
    """音色列表要把克隆状态透给前端（界面据此显示徽标/重试按钮）。"""
    from gui.web.api.voices import _to_summary

    row = _Row()
    row.clone_status = "failed"
    row.clone_error = "配额已用尽"

    s = _to_summary(row)
    assert s.clone_status == "failed"
    assert s.clone_error == "配额已用尽"
    assert s.minimax_voice_id == ""

# ============================================================
# 服务端 voice_id 生成规则（2026-10-21 实测出来的长度下限）
# ============================================================
@pytest.mark.parametrize(
    "local,expected",
    [
        ("yujialu", "yujialu_clone"),   # 7 字符 —— MiniMax 拒（"voice_id length"）
        ("ab", "ab_clone"),   # 8，正好达标
        ("a", "a0_clone"),
        ("jiangyu-4", "jiangyu-4"),     # 正好 8，原样
        ("Li-test-v1", "Li-test-v1"),   # 连字符可用（原样）
        ("my voice!", "my_voice_"),     # 非法字符 -> 下划线；7 字符 -> 再补 _clone
    ],
)
def test_minimax_server_id(local, expected):
    from gui.web.api.voices import _minimax_server_id

    assert _minimax_server_id(local) == expected


def test_minimax_server_id_always_meets_min_length():
    from gui.web.api.voices import (
        _minimax_server_id,
        MINIMAX_VOICE_ID_MIN_LEN,
    )

    for local in ("a", "ab", "x", "短", "yujialu", "a" * 100):
        assert len(_minimax_server_id(local)) >= MINIMAX_VOICE_ID_MIN_LEN


def test_short_id_clone_uses_generated_server_id(fake_cloner, ref_wav):
    """本地 ID 不合法时，服务端 ID 用生成的那个，不能拿 7 字符硬发。"""
    from gui.web.api.voices import _clone_to_minimax

    db = _DB()
    row = _Row(voice_id="yujialu", audio_path=str(ref_wav))

    res = _clone_to_minimax(db, row, "sk-test")

    assert res.clone_status == "done"
    assert res.minimax_voice_id == "yujialu_clone"


def test_duplicate_server_id_counts_as_done(fake_cloner, ref_wav):
    """
    MiniMax 不允许同名 voice_id，已存在时功能目标已达成 → 记 done。
    否则用户重录同名音色后就永远克隆不了了。
    """
    from gui.web.api.voices import _clone_to_minimax

    def _dup(wav_path, voice_id, api_key, timeout=60):
        from exceptions import VoiceCloneError
        raise VoiceCloneError("Clone API 错误: voice clone voice id duplicate")

    fake_cloner.clone = _dup

    db = _DB()
    row = _Row(voice_id="yujialu", audio_path=str(ref_wav))
    res = _clone_to_minimax(db, row, "sk-test")

    assert res.clone_status == "done"
    assert res.minimax_voice_id == "yujialu_clone"
    # 留痕："没覆盖"这件事要能被人看到（界面上是 tooltip）
    assert "未覆盖" in res.clone_error
