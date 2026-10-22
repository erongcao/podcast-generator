"""
voice_id / display_name 入参的 strip 行为回归测试（2026-10-22）

背景：web 看板上传用的是 prompt() 输入框，用户很自然会在 voice_id 前后带空格
（比如 "yao-1 "），而 VOICE_ID_PATTERN 不允许空白 —— 表现是前端弹一句
"voice_id 格式非法"，后端没有任何 POST 记录，看起来就像"加不进去"。
（之前的 Qt 面板显式 .strip()，web 没有，前后端都没 strip，是空子。）

这里锁住三层：
  1. 前端 prompt 的输入会被 trim
  2. 后端 add_voice_impl 也会 strip（覆盖 API 调用方 / 测试直接打）
  3. 纯空白 voice_id 会被明确拒绝

失败/解码路径的 400 在解码之前就要把 voice_id strip 掉，所以这些测试
不需要真实音频 —— 用空 bytes 走"音频文件为空"分支就够了。
"""
import shutil
import subprocess
from pathlib import Path

import pytest


# 真实 wav 路径要 ffmpeg；其他测试不需要
NEEDS_FFMPEG = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="需要 ffmpeg"
)


def _empty_file_check_raises(monkeypatch, **kwargs):
    """给一个空 file_bytes，让 add_voice_impl 在 strip 之后撞上
    '上传的音频文件为空' 分支，从而验证前面的 strip 行为。"""
    from gui.web.api import voices as voices_api
    monkeypatch.setattr(voices_api, "LEGACY_AUDIO_DIR", Path("/tmp"))
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        voices_api.add_voice_impl(file_bytes=b"", **kwargs)
    return exc


def test_voice_id_with_surrounding_whitespace_is_trimmed(monkeypatch):
    """' yao-1 ' 视为 'yao-1'（验证通过，落到下一关 = 空文件）。"""
    exc = _empty_file_check_raises(
        monkeypatch,
        voice_id="  yao-1  ",
        display_name="",
    )
    # 走到 file_bytes 检查 = voice_id regex 验证通过了
    assert exc.value.status_code == 400
    assert "音频文件为空" in str(exc.value.detail)
    assert "格式" not in str(exc.value.detail)


def test_voice_id_all_whitespace_rejected_with_clear_message(monkeypatch):
    """纯空白不能当成 'yao-1' 蒙混过关。"""
    from gui.web.api import voices as voices_api
    monkeypatch.setattr(voices_api, "LEGACY_AUDIO_DIR", Path("/tmp"))
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        voices_api.add_voice_impl(voice_id="    ", display_name="", file_bytes=b"x")
    assert exc.value.status_code == 400
    assert "voice_id 不能为空" in str(exc.value.detail)


def test_voice_id_with_leading_space_fails_validation_without_trim(monkeypatch):
    """防御性测试：万一 strip 漏掉，前导空格应该被正则拒（说明 fix 在工作）。"""
    from gui.web.api import voices as voices_api
    monkeypatch.setattr(voices_api, "LEGACY_AUDIO_DIR", Path("/tmp"))
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        voices_api.add_voice_impl(voice_id=" yao-1", display_name="", file_bytes=b"x")
    # 若 fix 生效：走到 '音频文件为空'（已经 strip）
    # 若 fix 失效：走到 'voice_id 非法'
    detail = str(exc.value.detail)
    assert "音频文件为空" in detail or "格式非法" not in detail


def test_display_name_with_surrounding_whitespace_is_trimmed(monkeypatch):
    """display_name 也 strip，避免 '  姚 ' 之类空白。"""
    from gui.web.api import voices as voices_api
    monkeypatch.setattr(voices_api, "LEGACY_AUDIO_DIR", Path("/tmp"))
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        voices_api.add_voice_impl(voice_id="yao-1", display_name="  姚  ", file_bytes=b"")
    assert exc.value.status_code == 400
    # 走到 '音频文件为空' = display_name 被 regex/字段检查接受
    # （display_name 不走 regex，只 strip 然后视为空时回退到 voice_id）
    assert "音频文件为空" in str(exc.value.detail)


@NEEDS_FFMPEG
def test_trimmed_voice_id_lands_on_disk_as_trimmed(tmp_path, monkeypatch):
    """端到端：上传成功时，磁盘上写的是 trim 后的文件名（不是 '  yao-1  .wav'）。"""
    import wave
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from gui.web.api import voices as voices_api
    from gui.web.db import Base
    from gui.web.models import User, VoiceClone

    # 1s 正弦 wav 当上传内容
    src = tmp_path / "src.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=1", str(src)],
        check=True, capture_output=True, timeout=60,
    )
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    monkeypatch.setattr(voices_api, "LEGACY_AUDIO_DIR", audio_dir)

    # 独立临时库，不能碰真库（真库里已经有 yao-1 了）
    engine = create_engine(f"sqlite:///{tmp_path/'test.db'}", future=True)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, future=True)
    with TestingSession() as s:
        s.add(User(username="trim-test", password_hash="x", is_admin=True))
        s.commit()
    monkeypatch.setattr(voices_api, "SessionLocal", TestingSession)

    # 短路 MiniMax 克隆 + 取 key，不碰真 API
    monkeypatch.setattr(
        voices_api, "_clone_to_minimax", lambda *a, **k: _FakeCloneResponse()
    )
    monkeypatch.setattr(
        "gui.web.api.synthesize._load_minimax_key", lambda: "sk-test-dummy"
    )

    res = voices_api.add_voice_impl(
        voice_id="  yao-1  ",
        display_name="  姚  ",
        file_bytes=src.read_bytes(),
    )

    assert res.voice_id == "yao-1", "返回值要用 trim 后的 ID"
    assert (audio_dir / "yao-1.wav").exists(), "trim 后的文件名应该落盘"
    assert not (audio_dir / "  yao-1  .wav").exists(), "原始空白不能落盘"

    with wave.open(str(audio_dir / "yao-1.wav")) as w:
        assert w.getnchannels() == 1
        assert w.getframerate() == 48000

    with TestingSession() as s:
        row = s.query(VoiceClone).filter(VoiceClone.voice_id == "yao-1").first()
        assert row is not None
        assert row.display_name == "姚", "display_name 也要 trim"


class _FakeCloneResponse:
    clone_status = "skipped"
    minimax_voice_id = ""
    clone_error = "test stub"
