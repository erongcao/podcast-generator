#!/usr/bin/env python3
"""
音色库：CRUD + 原子 JSON 持久化
存于 ~/Library/Application Support/PodcastGenerator/voices/
"""
import json
import os
import shutil
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from exceptions import LibraryError

VOICES_DIR = Path.home() / "Library" / "Application Support" / "PodcastGenerator" / "voices"
VOICES_JSON = VOICES_DIR / "voices.json"
AUDIO_DIR = VOICES_DIR / "audio"

_lock = threading.Lock()


def init():
    """创建目录、初始化 voices.json；损坏时备份并重置。"""
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    with _lock:
        if not VOICES_JSON.exists():
            _atomic_write({"voices": []})
            return
        try:
            json.loads(VOICES_JSON.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            backup = VOICES_DIR / "voices.json.bak"
            shutil.copy2(VOICES_JSON, backup)
            _atomic_write({"voices": []})


def _read():
    """读取并解析 voices.json。"""
    return json.loads(VOICES_JSON.read_text(encoding="utf-8"))


def list_all():
    """返回所有音色的列表（按创建时间倒序）。

    命名说明：原本叫 ``list()``，但会遮蔽 Python 内置 ``list()``，导致
    模块内 / 调用方代码里凡是 ``list(some_iterable)`` 都会因签名不匹配崩
    ("list() takes 0 positional arguments but 1 was given")。改名 ``list_all``
    是修这个 shadowing bug；保留 ``list`` 作为公开别名以便老代码平滑迁移。
    """
    init()
    data = _read()
    return sorted(data.get("voices", []), key=lambda v: v.get("created_at", ""), reverse=True)


# 向后兼容别名：允许老代码用 ``voice_library.list()``，但调用方应该改用 list_all。
# 注意：这里不能用 ``list`` 作为变量名（会再次遮蔽 builtin），所以命名为 ``_list_alias``，
# 真正对外暴露时通过模块 __getattr__（Python 3.7+）实现。
_list_alias = list_all


def __getattr__(name):
    """模块级 __getattr__ 让 ``voice_library.list()`` 仍可工作，但提示迁移。"""
    if name == "list":
        import warnings
        warnings.warn(
            "voice_library.list() 已重命名为 list_all()；老代码仍可工作但建议迁移。",
            DeprecationWarning,
            stacklevel=2,
        )
        return _list_alias
    raise AttributeError(f"module 'voice_library' has no attribute {name!r}")


def get(voice_id):
    """按 voice_id 获取单条；找不到返回 None。"""
    for v in list_all():
        if v["voice_id"] == voice_id:
            return v
    return None


def exists(voice_id):
    return get(voice_id) is not None


def add(voice_id, display_name, wav_path, notes="", ref_text="", tags=None):
    """新增音色：拷贝 WAV 到 AUDIO_DIR、追加到索引。返回完整记录。

    Args:
        ref_text: 参考音频的转写文字。Qwen3-TTS 本地克隆时必填；
                  MiniMax 路径不需要（服务端基于声纹自动处理）。
                  留空时存 `` 字段为 None，保持向后兼容。
        tags: 标签列表，例如 ["female"]、["foreign", "english"]。
              下游合成（voxcpm_tts）按 tag 决定是否走后处理。
              留空存为 []，保持向后兼容。
    """
    if exists(voice_id):
        raise LibraryError(f"voice_id 已存在: {voice_id}")
    init()
    src = Path(wav_path)
    if not src.exists():
        raise LibraryError(f"源 WAV 不存在: {wav_path}")
    dest = AUDIO_DIR / f"{voice_id}.wav"
    shutil.copy2(src, dest)
    record = {
        "voice_id": voice_id,
        "display_name": display_name,
        "wav_path": str(dest),
        "notes": notes,
        "tags": list(tags or []),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if ref_text:
        record["ref_text"] = ref_text
    with _lock:
        data = _read()
        data.setdefault("voices", []).append(record)
        _atomic_write(data)
    return record


def add_designed(voice_id, display_name, preview_audio, notes="", tags=None):
    """Add a MiniMax Voice-Design-generated voice (no source WAV on disk).

    Designed voices live on the MiniMax server; we keep only the preview MP3
    locally so the user can replay it later. wav_path is stored as None.

    Args:
        voice_id: API voice_id (str, must match VOICE_ID_PATTERN rules)
        display_name: human-readable label shown in UI
        preview_audio: raw MP3 bytes returned by voice_design API
        notes: optional free-text note (e.g., the original prompt)
        tags: 标签列表，同 add()。常见："female"、"voice_design"。

    Returns:
        The new library record dict.
    """
    if exists(voice_id):
        raise LibraryError(f"voice_id 已存在: {voice_id}")
    init()
    preview_path = AUDIO_DIR / f"{voice_id}.preview.mp3"
    preview_path.write_bytes(preview_audio or b"")
    record = {
        "voice_id": voice_id,
        "display_name": display_name,
        "wav_path": None,
        "preview_path": str(preview_path),
        "source": "voice_design",
        "notes": notes,
        "tags": list(tags or []),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with _lock:
        data = _read()
        data.setdefault("voices", []).append(record)
        _atomic_write(data)
    return record


def delete(voice_id):
    """删除音色（含 WAV 文件）。"""
    record = get(voice_id)
    if not record:
        return
    wav = Path(record["wav_path"]) if record.get("wav_path") else None
    if wav and wav.exists():
        wav.unlink()
    preview = Path(record["preview_path"]) if record.get("preview_path") else None
    if preview and preview.exists():
        preview.unlink()
    with _lock:
        data = _read()
        data["voices"] = [v for v in data["voices"] if v["voice_id"] != voice_id]
        _atomic_write(data)


def update(voice_id, **fields):
    """更新 display_name / notes / ref_text / tags / minimax_voice_id（不允许改 voice_id；不允许改 wav_path）。
    minimax_voice_id: MiniMax 服务端 voice_id（语音克隆后从 /v1/voice_clone 返回）。
                       设为非空后，minimax_tts 路径会用这个 ID 调 /v1/t2a_v2，
                       而不是把本地 voice_id 当作 MiniMax 服务器 ID 报错。
    """
    allowed = {"display_name", "notes", "ref_text", "tags", "minimax_voice_id"}
    bad = set(fields.keys()) - allowed
    if bad:
        raise LibraryError(f"update 不允许字段: {bad}")
    with _lock:
        data = _read()
        for v in data["voices"]:
            if v["voice_id"] == voice_id:
                v.update(fields)
                _atomic_write(data)
                return
        raise LibraryError(f"voice_id 不存在: {voice_id}")


def _atomic_write(data):
    """原子写入 JSON：写临时文件再 rename。"""
    VOICES_JSON.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=VOICES_JSON.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, VOICES_JSON)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
