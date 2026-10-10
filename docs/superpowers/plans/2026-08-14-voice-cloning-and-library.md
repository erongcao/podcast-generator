# Voice Cloning & Custom Voice Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a microphone-based voice cloning flow and a user-global voice library to the podcast generator, plus integrate library voices into the existing speakers panel.

**Architecture:** Three pure modules (recorder, cloner, library) with clean interfaces, tested independently via pytest with mocks. One GUI panel glues them together. Existing `speakers.py` gets one new dropdown entry; nothing else in the existing code is rewritten.

**Tech Stack:** Python 3, customtkinter, sounddevice, soundfile, requests, pytest. MiniMax clone API (endpoint verified in Task 1).

**Reference spec:** `docs/superpowers/specs/2026-08-14-voice-cloning-and-library-design.md`

---

## File Structure

### New files

| Path | Responsibility |
|------|----------------|
| `voice_recorder.py` | Mic capture → WAV file. No GUI, no API. |
| `voice_cloner.py` | MiniMax clone API client + test synthesis call. No GUI. |
| `voice_library.py` | CRUD over voices, atomic JSON persistence. No GUI. |
| `gui/panels/voice_library_panel.py` | Clone UI, library list, `VoicePickerModal`. Only GUI layer. |
| `tests/__init__.py` | Test package marker. |
| `tests/conftest.py` | Shared fixtures (temp library dir, mock API). |
| `tests/test_voice_library.py` | Unit tests for `voice_library.py`. |
| `tests/test_voice_recorder.py` | Unit tests for `voice_recorder.py` (sounddevice mocked). |
| `tests/test_voice_cloner.py` | Unit tests for `voice_cloner.py` (HTTP mocked). |
| `tests/test_integration.py` | End-to-end clone → pick → synthesize (all externals mocked). |
| `pytest.ini` | pytest config (testpaths, asyncio mode off). |

### Modified files

| Path | Change |
|------|--------|
| `exceptions.py` | Add `VoiceCloneError`, `LibraryError`, `RecorderError` (each subclassing `PodcastGeneratorError`). |
| `gui/panels/speakers.py` | Add `("__from_library__", "📚 从音色库选")` to `MINIMAX_VOICES`. Update `VoiceConfigRow.get_voice_id()` to open `VoicePickerModal` when this option is chosen. |
| `gui/panels/input.py` | Grid `voice_library_panel` below credentials panel. Add engine-aware gray-out binding. |

### Helper reuse note

`credentials.py:_atomic_write_json` is private (underscore-prefixed). The plan promotes a copy into `voice_library.py` rather than refactor `credentials.py`. Document this in the implementation commit message.

---

## Task 1: Verify MiniMax clone API contract

**Files:** None (research task — produce a doc comment)

**Why first:** Every later task that touches `voice_cloner.py` depends on knowing the real endpoint, request shape, and auth header. Doing this first prevents rewriting later.

- [ ] **Step 1: Locate MiniMax voice cloning docs**

Open https://platform.MiniMax.io/document/voice-cloning (or equivalent) via WebFetch. Identify:
- Endpoint URL (likely `/v1/voice_clone` or `/v1/voices/clone`).
- HTTP method.
- Auth header pattern (likely `Authorization: Bearer <api_key>`, matching existing `minimax_tts.py`).
- Request body shape: multipart upload? JSON with base64? Required fields (audio file, voice_id, sample_text, model?).
- Success response shape.
- Error codes (400/401/429/...).

- [ ] **Step 2: Verify with a live curl against your account**

Using the API key already in `config.json` (region=cn, per prior memory), run a minimal clone request with a 10-second pre-recorded WAV. Confirm a success response is returned.

```bash
# Template — adjust fields per docs from Step 1
curl -X POST https://api.minimaxi.com/v1/voice_clone \
  -H "Authorization: Bearer $MINIMAX_KEY" \
  -F "voice_id=test_probe_$(date +%s)" \
  -F "audio=@/tmp/probe.wav" \
  -F "sample_text=这是一段测试" \
  | tee /tmp/clone_probe.json
```

Expected: `{"base_resp":{"status_code":0,...}, ...}` or equivalent success shape.

- [ ] **Step 2b: Verify cloned voice_id works through the synthesis path**

Recent commit `a7539e7 fix: update MiniMax voice IDs to match mmx CLI` shows CLI/API voice-ID mismatches have bitten this project. Test that the cloned voice_id can be used by **both** the CLI and API synthesis paths.

```bash
# Test API path (works because clone + TTS are on the same endpoint)
curl -X POST https://api.minimaxi.com/v1/t2a_v2 \
  -H "Authorization: Bearer $MINIMAX_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"speech-2.8-hd","text":"合成测试","stream":false,
       "voice_setting":{"voice_id":"test_probe_xxx","speed":1,"vol":1,"pitch":0},
       "audio_setting":{"sample_rate":32000,"bitrate":128000,"format":"mp3","channel":1},
       "output_format":"hex"}'

# Test CLI path (this is the one that has historically diverged)
mmx speech synthesize --text "合成测试" \
  --voice test_probe_xxx --non-interactive --out /tmp/probe_synth.mp3
```

Expected: both return audio (API returns hex, CLI writes file). If CLI fails with "invalid voice id", document this — `voice_cloner.py:synthesize_test` must skip the CLI path and go straight to API.

- [ ] **Step 3: Delete the probe voice** (if the API provides a delete endpoint, per docs)

If no delete endpoint exists, document this — it means voice_id namespace is forever-polluted by failed probes.

- [ ] **Step 4: Write findings as a docstring at the top of `voice_cloner.py`**

```python
"""
MiniMax voice cloning API client.

Verified endpoint (YYYY-MM-DD):
  POST https://api.minimaxi.com/v1/voice_clone
  Headers: Authorization: Bearer <api_key>
  Body (multipart/form-data):
    - voice_id: string (user-chosen, ASCII, unique)
    - audio: file (WAV 16kHz mono 16-bit)
    - sample_text: string (text used for synthesis, ≥ 10 chars)
  Success: 200 + {"base_resp": {"status_code": 0, ...}, "voice_id": "..."}
  Errors:  400 (invalid audio/voice_id), 401 (bad key), 429 (rate limit)
"""
```

- [ ] **Step 5: Commit**

```bash
git commit --allow-empty -m "chore: verify MiniMax clone API contract (incl. CLI path)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: Add new exception classes

**Files:**
- Modify: `exceptions.py`

- [ ] **Step 1: Add three new exception classes at the bottom of `exceptions.py`**

Append after the existing classes:

```python
class VoiceCloneError(PodcastGeneratorError):
    """MiniMax 音色克隆 API 错误"""

    def __init__(self, message, status_code=None, trace_id=None):
        self.status_code = status_code
        super().__init__(message, trace_id)


class LibraryError(PodcastGeneratorError):
    """音色库文件 I/O 或数据错误"""
    pass


class RecorderError(PodcastGeneratorError):
    """麦克风录音错误"""
    pass
```

- [ ] **Step 2: Verify import works**

Run:
```bash
python -c "from exceptions import VoiceCloneError, LibraryError, RecorderError; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add exceptions.py
git commit -m "feat: add VoiceCloneError, LibraryError, RecorderError

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 3: Set up test infrastructure + dependencies

**Files:**
- Create: `pytest.ini`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `requirements.txt` (does not exist in repo today)

- [ ] **Step 1: Create `requirements.txt` at the project root**

```
# Existing runtime deps (verify against current code before adding)
customtkinter
requests
urllib3

# New for voice cloning feature
sounddevice
soundfile

# Test deps
pytest
```

- [ ] **Step 2: Install everything from `requirements.txt`**

```bash
pip install -r requirements.txt
```

Expected: install succeeds. If `sounddevice` install fails on macOS (portaudio missing), `brew install portaudio` then retry.

- [ ] **Step 3: Create `pytest.ini`**

```ini
[pytest]
testpaths = tests
python_files = test_*.py
addopts = -v --tb=short
```

- [ ] **Step 4: Create `tests/__init__.py`** (empty file)

```bash
touch tests/__init__.py
```

- [ ] **Step 5: Create `tests/conftest.py` with shared fixtures**

```python
"""Shared pytest fixtures."""
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def temp_voices_dir(monkeypatch):
    """Redirect voice_library to a temp dir for isolation."""
    tmp = Path(tempfile.mkdtemp(prefix="voices_test_"))
    monkeypatch.setattr("voice_library.VOICES_DIR", tmp)
    monkeypatch.setattr("voice_library.VOICES_JSON", tmp / "voices.json")
    monkeypatch.setattr("voice_library.AUDIO_DIR", tmp / "audio")
    return tmp


@pytest.fixture
def mock_sounddevice(monkeypatch):
    """Replace sounddevice with a fake that yields silence."""
    fake = MagicMock()
    fake.rec = MagicMock(return_value=(MagicMock(), 0))
    fake.query_devices = MagicMock(return_value=[{"name": "MacBook Pro Mic", "max_input_channels": 1}])
    monkeypatch.setattr("voice_recorder.sd", fake)
    return fake


@pytest.fixture
def mock_requests_post(monkeypatch):
    """Replace requests.post with a configurable mock."""
    fake = MagicMock()
    monkeypatch.setattr("voice_cloner.requests.post", fake)
    return fake
```

- [ ] **Step 6: Run pytest to verify setup**

```bash
pytest
```
Expected: collects 0 tests, exit 0 ("no tests ran" or "1 warning" about test collection).

- [ ] **Step 7: Commit**

```bash
git add requirements.txt pytest.ini tests/
git commit -m "chore: add requirements.txt and pytest fixtures

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 4: Implement `voice_library.py` with TDD

**Files:**
- Create: `voice_library.py`
- Create: `tests/test_voice_library.py`

- [ ] **Step 1: Write failing tests for CRUD + atomic write**

Create `tests/test_voice_library.py`:

```python
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
    voices = vl.list()
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
    assert len(vl.list()) == 10
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
pytest tests/test_voice_library.py
```
Expected: ModuleNotFoundError or collection error (voice_library doesn't exist yet).

- [ ] **Step 3: Implement `voice_library.py`**

```python
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


def list():
    """返回所有音色的列表（按创建时间倒序）。"""
    init()
    data = _read()
    return sorted(data.get("voices", []), key=lambda v: v.get("created_at", ""), reverse=True)


def get(voice_id):
    """按 voice_id 获取单条；找不到返回 None。"""
    for v in list():
        if v["voice_id"] == voice_id:
            return v
    return None


def exists(voice_id):
    return get(voice_id) is not None


def add(voice_id, display_name, wav_path, notes=""):
    """新增音色：拷贝 WAV 到 AUDIO_DIR、追加到索引。返回完整记录。"""
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
    wav = Path(record["wav_path"])
    if wav.exists():
        wav.unlink()
    with _lock:
        data = _read()
        data["voices"] = [v for v in data["voices"] if v["voice_id"] != voice_id]
        _atomic_write(data)


def update(voice_id, **fields):
    """更新 display_name / notes（不允许改 voice_id；不允许改 wav_path）。"""
    allowed = {"display_name", "notes"}
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
```

- [ ] **Step 4: Run tests, verify all pass**

```bash
pytest tests/test_voice_library.py
```
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add voice_library.py tests/test_voice_library.py
git commit -m "feat(voice_library): CRUD + atomic JSON persistence

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 5: Implement `voice_recorder.py` with TDD

**Files:**
- Create: `voice_recorder.py`
- Create: `tests/test_voice_recorder.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_voice_recorder.py`:

```python
"""Tests for voice_recorder module."""
import os
import wave
from pathlib import Path
import pytest

import voice_recorder as vr
from exceptions import RecorderError


def test_record_produces_valid_wav(mock_sounddevice, tmp_path):
    out = vr.record(seconds=2, output_path=str(tmp_path / "out.wav"))
    assert Path(out).exists()
    with wave.open(out, "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 16000


def test_record_enforces_min_duration():
    with pytest.raises(RecorderError, match="10 秒"):
        vr.record(seconds=5)


def test_record_enforces_max_duration():
    with pytest.raises(RecorderError, match="60 秒"):
        vr.record(seconds=120)


def test_record_cleans_temp_on_error(mock_sounddevice, monkeypatch, tmp_path):
    """如果 sounddevice 抛错，临时文件应被删除。"""
    mock_sounddevice.rec.side_effect = RuntimeError("mic busy")
    out = str(tmp_path / "out.wav")
    with pytest.raises(RecorderError):
        vr.record(seconds=15, output_path=out)
    assert not Path(out).exists()


def test_list_devices_returns_list(mock_sounddevice):
    mock_sounddevice.query_devices.return_value = [{"name": "MacBook Pro Mic"}]
    devices = vr.list_input_devices()
    assert isinstance(devices, list)
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
pytest tests/test_voice_recorder.py
```
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement `voice_recorder.py`**

```python
#!/usr/bin/env python3
"""
麦克风录音 → WAV 文件（16kHz mono 16-bit）
"""
import os
import tempfile
from pathlib import Path

import sounddevice as sd
import soundfile as sf

from exceptions import RecorderError

MIN_SECONDS = 10
MAX_SECONDS = 60
SAMPLE_RATE = 16000
CHANNELS = 1


def list_input_devices():
    """返回所有可用的输入设备名列表。"""
    devices = sd.query_devices()
    return [
        d["name"] for d in devices
        if isinstance(d, dict) and d.get("max_input_channels", 0) > 0
    ]


def record(seconds, output_path=None, sample_rate=SAMPLE_RATE):
    """
    录 seconds 秒到 WAV 文件。返回文件路径。
    seconds 必须在 [MIN_SECONDS, MAX_SECONDS] 范围。
    """
    if seconds < MIN_SECONDS:
        raise RecorderError(f"录音太短，至少 {MIN_SECONDS} 秒")
    if seconds > MAX_SECONDS:
        raise RecorderError(f"录音太长，最多 {MAX_SECONDS} 秒")
    if output_path is None:
        fd, output_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
    try:
        frames = sd.rec(
            int(seconds * sample_rate),
            samplerate=sample_rate,
            channels=CHANNELS,
            dtype="int16",
        )
        sd.wait()
        sf.write(output_path, frames, sample_rate, subtype="PCM_16")
        return output_path
    except Exception as e:
        if os.path.exists(output_path):
            os.unlink(output_path)
        raise RecorderError(f"录音失败: {e}") from e
```

- [ ] **Step 4: Run tests, verify all pass**

```bash
pytest tests/test_voice_recorder.py
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add voice_recorder.py tests/test_voice_recorder.py
git commit -m "feat(voice_recorder): mic capture wrapper with min/max duration guards

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 6: Implement `voice_cloner.py` with TDD

**Files:**
- Create: `voice_cloner.py`
- Create: `tests/test_voice_cloner.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_voice_cloner.py`:

```python
"""Tests for voice_cloner module (HTTP mocked)."""
import pytest

import voice_cloner as vc
from exceptions import VoiceCloneError


def test_clone_success_returns_voice_id(mock_requests_post, tmp_path):
    mock_requests_post.return_value.status_code = 200
    mock_requests_post.return_value.json.return_value = {
        "base_resp": {"status_code": 0, "status_msg": "ok"},
        "voice_id": "my_voice",
    }
    wav = tmp_path / "in.wav"
    wav.write_bytes(b"fake wav bytes")
    result = vc.clone(wav_path=str(wav), voice_id="my_voice", sample_text="测试", api_key="sk-test")
    assert result == "my_voice"


def test_clone_raises_on_non_200(mock_requests_post, tmp_path):
    mock_requests_post.return_value.status_code = 401
    mock_requests_post.return_value.json.return_value = {"msg": "invalid api key"}
    wav = tmp_path / "in.wav"
    wav.write_bytes(b"x")
    with pytest.raises(VoiceCloneError, match="401"):
        vc.clone(str(wav), "v1", "测试", "sk-bad")


def test_clone_raises_on_api_error_status(mock_requests_post, tmp_path):
    mock_requests_post.return_value.status_code = 200
    mock_requests_post.return_value.json.return_value = {
        "base_resp": {"status_code": 1, "status_msg": "invalid audio format"},
    }
    wav = tmp_path / "in.wav"
    wav.write_bytes(b"x")
    with pytest.raises(VoiceCloneError, match="invalid audio"):
        vc.clone(str(wav), "v1", "测试", "sk-test")


def test_clone_429_raises_with_status(mock_requests_post, tmp_path):
    mock_requests_post.return_value.status_code = 429
    wav = tmp_path / "in.wav"
    wav.write_bytes(b"x")
    with pytest.raises(VoiceCloneError) as exc:
        vc.clone(str(wav), "v1", "测试", "sk-test")
    assert exc.value.status_code == 429


def test_synthesize_test_returns_bytes(mock_requests_post):
    """synthesize_test delegates to existing minimax_tts.synthesize — verify it does NOT call clone endpoint."""
    import minimax_tts
    from unittest.mock import patch

    with patch.object(minimax_tts, "synthesize", return_value=b"mp3bytes") as mock_synth:
        result = vc.synthesize_test(voice_id="v1", text="hi", api_key="sk-test")
    assert result == b"mp3bytes"
    mock_synth.assert_called_once()
    # verify it did NOT hit clone endpoint
    mock_requests_post.assert_not_called()
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
pytest tests/test_voice_cloner.py
```
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement `voice_cloner.py`**

(Endpoint URL filled from Task 1's probe. Use the actual URL discovered.)

```python
#!/usr/bin/env python3
"""
MiniMax 复刻音色 API 客户端。

[Endpoint verified YYYY-MM-DD per Task 1]
"""
from pathlib import Path

import requests

import minimax_tts
from exceptions import VoiceCloneError

CLONE_URL = "https://api.minimaxi.com/v1/voice_clone"  # ← adjust to Task 1 finding


def clone(wav_path, voice_id, sample_text, api_key, timeout=60):
    """
    上传 WAV 到 MiniMax 创建复刻音色。
    返回 voice_id；失败抛 VoiceCloneError。
    """
    wav_path = Path(wav_path)
    if not wav_path.exists():
        raise VoiceCloneError(f"WAV 文件不存在: {wav_path}")

    with open(wav_path, "rb") as f:
        resp = requests.post(
            CLONE_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            files={"audio": (wav_path.name, f, "audio/wav")},
            data={"voice_id": voice_id, "sample_text": sample_text},
            timeout=timeout,
        )

    if resp.status_code != 200:
        raise VoiceCloneError(
            f"HTTP {resp.status_code}: {resp.text[:200]}",
            status_code=resp.status_code,
        )

    try:
        data = resp.json()
    except ValueError as e:
        raise VoiceCloneError(f"Invalid JSON response: {e}")

    base = data.get("base_resp", {})
    if base.get("status_code", -1) != 0:
        raise VoiceCloneError(
            f"API 错误: {base.get('status_msg', 'unknown')}",
            status_code=resp.status_code,
            trace_id=data.get("trace_id"),
        )

    return data.get("voice_id", voice_id)


def synthesize_test(voice_id, text, api_key):
    """
    用指定 voice_id 合成一段测试音频。委托给 minimax_tts.synthesize。
    返回 mp3 bytes。
    """
    return minimax_tts.synthesize(
        text=text,
        voice_id=voice_id,
        api_key=api_key,
    )
```

- [ ] **Step 4: Run tests, verify all pass**

```bash
pytest tests/test_voice_cloner.py
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add voice_cloner.py tests/test_voice_cloner.py
git commit -m "feat(voice_cloner): MiniMax clone API client + test synthesis

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 7: Integration test — full clone → pick → synthesize flow

**Files:**
- Create: `tests/test_integration.py`

- [ ] **Step 1: Write the integration test**

```python
"""End-to-end test: clone a voice, pick from library, synthesize with it."""
from unittest.mock import patch
import pytest

import voice_library as vl
import voice_cloner as vc
from exceptions import LibraryError


def test_clone_then_pick_then_synthesize(temp_voices_dir, tmp_path, mock_requests_post):
    """All externals mocked. Verifies the glue between modules."""
    # Setup: an external WAV to "clone"
    wav = tmp_path / "sample.wav"
    wav.write_bytes(b"fake wav bytes")

    # Mock clone API: success
    mock_requests_post.return_value.status_code = 200
    mock_requests_post.return_value.json.return_value = {
        "base_resp": {"status_code": 0},
        "voice_id": "doctor_voice",
    }

    # 1. Clone
    voice_id = vc.clone(str(wav), voice_id="doctor_voice", sample_text="测试朗读", api_key="sk-test")
    assert voice_id == "doctor_voice"

    # 2. Add to library
    vl.add(voice_id, "医生声音", str(wav), notes="测试用")

    # 3. List and pick
    library = vl.list()
    assert any(v["voice_id"] == "doctor_voice" for v in library)

    # 4. Synthesize a test phrase with the new voice
    with patch("voice_cloner.minimax_tts.synthesize", return_value=b"mp3") as mock_synth:
        result = vc.synthesize_test("doctor_voice", "你好", api_key="sk-test")
    assert result == b"mp3"
    mock_synth.assert_called_once()
    call_kwargs = mock_synth.call_args.kwargs
    assert call_kwargs["voice_id"] == "doctor_voice"


def test_duplicate_voice_id_rejected(temp_voices_dir, tmp_path):
    """Adding the same voice_id twice should raise LibraryError."""
    vl.init()
    wav = tmp_path / "x.wav"
    wav.write_bytes(b"x")
    vl.add("dup", "甲", str(wav))
    with pytest.raises(LibraryError, match="已存在"):
        vl.add("dup", "乙", str(wav))


def test_delete_then_re_add_works(temp_voices_dir, tmp_path):
    """After delete, the same voice_id can be re-added."""
    vl.init()
    wav = tmp_path / "x.wav"
    wav.write_bytes(b"x")
    vl.add("re", "甲", str(wav))
    vl.delete("re")
    vl.add("re", "乙", str(wav))  # should not raise
    assert vl.exists("re")
```

- [ ] **Step 2: Run tests, verify all pass**

```bash
pytest tests/test_integration.py
```
Expected: 3 passed.

- [ ] **Step 3: Run full test suite to ensure no regressions**

```bash
pytest
```
Expected: all tests pass (4 files × N tests).

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "test(voice): integration test for clone → library → synthesize

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 8: Implement `voice_library_panel.py` (GUI)

**Files:**
- Create: `gui/panels/voice_library_panel.py`

No automated GUI tests in this plan (manual smoke test in Task 11).

- [ ] **Step 1: Build the `VoiceLibraryPanel`, `CloneDialog`, and `VoicePickerModal` classes**

```python
#!/usr/bin/env python3
"""
音色库面板：克隆 UI + 库列表 + VoicePickerModal

Spec requirements addressed here:
  - Countdown UI during recording (big numeric + progress bar)
  - Required post-record preview (no skip)
  - Configurable recording duration (10-60s, default 30s)
  - Separate ASCII-validated voice_id field (not derived from Chinese display_name)
  - Overwrite race fix: clone API first, only delete old voice on success
  - messagebox imported (otherwise AttributeError on error paths)
"""
import os
import re
import subprocess
import tempfile
import threading
import tkinter as tk
from tkinter import simpledialog, messagebox
from pathlib import Path

import customtkinter as ctk

from gui.theme import GHIBLI

import voice_library as vl
import voice_recorder as vr
import voice_cloner as vc


SAMPLE_TEXTS = {
    "通用": "今天我们来聊一聊健康与生活。请大家保持规律的作息和良好的心态。",
    "医疗": "高血压患者应定期监测血压，每日定时服药，保持低盐饮食和适度运动。",
    "慢速清晰": "声——音——克——隆。请——慢——速——清——晰——地——朗——读——每——个——字。",
}

DEFAULT_DURATION = 30
DURATION_OPTIONS = [15, 20, 30, 45, 60]
VOICE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{3,32}$")

LIBRARY_GRAYOUT_TOOLTIP = "豆包引擎不支持音色克隆，请切换到 MiniMax"


class VoiceLibraryPanel(ctk.CTkFrame):
    def __init__(self, parent, get_engine, get_api_key, **kwargs):
        super().__init__(parent, **kwargs)
        self.get_engine = get_engine
        self.get_api_key = get_api_key
        self._build_ui()
        self._refresh_list()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self, text="📚 音色库（MiniMax）",
            font=ctk.CTkFont("Helvetica Neue", 13, "bold"),
            text_color=GHIBLI["text"],
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(8, 4))

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 4))
        btn_frame.grid_columnconfigure(0, weight=1)
        btn_frame.grid_columnconfigure(1, weight=0)

        self.clone_btn = ctk.CTkButton(
            btn_frame, text="🎙️ 克隆新音色",
            fg_color=GHIBLI["grass"], hover_color=GHIBLI["forest"],
            text_color=GHIBLI["white"], corner_radius=6,
            command=self._open_clone_dialog,
        )
        self.clone_btn.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        self.refresh_btn = ctk.CTkButton(
            btn_frame, text="🔄", width=40,
            fg_color=GHIBLI["cream"], hover_color=GHIBLI["dust"],
            text_color=GHIBLI["text"], corner_radius=6,
            command=self._refresh_list,
        )
        self.refresh_btn.grid(row=0, column=1, sticky="e", padx=(4, 0))

        self.list_frame = ctk.CTkScrollableFrame(
            self, fg_color=GHIBLI["cream"], corner_radius=6, height=180,
        )
        self.list_frame.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 8))
        self.grid_rowconfigure(2, weight=1)
        self.list_frame.grid_columnconfigure(0, weight=1)

    def _refresh_list(self):
        for w in self.list_frame.winfo_children():
            w.destroy()
        voices = vl.list()
        if not voices:
            ctk.CTkLabel(
                self.list_frame, text="（还没有音色，点击「克隆新音色」开始）",
                text_color=GHIBLI["text_light"],
                font=ctk.CTkFont("Helvetica Neue", 11),
            ).grid(row=0, column=0, padx=10, pady=10)
            return
        for i, v in enumerate(voices):
            row = _VoiceRow(
                self.list_frame, v,
                on_play=self._play_voice, on_delete=self._delete_voice,
            )
            row.grid(row=i, column=0, sticky="ew", padx=4, pady=2)

    def _open_clone_dialog(self):
        if self.get_engine() != "minimax":
            return
        api_key = self.get_api_key()
        if not api_key:
            messagebox.showerror("错误", "请先在凭证面板填写 MiniMax API Key")
            return
        CloneDialog(self, api_key=api_key, on_success=self._refresh_list)

    def _play_voice(self, voice_id):
        text = simpledialog.askstring(
            "试听", "输入测试文本：", initialvalue="这是一个测试",
            parent=self.winfo_toplevel(),
        )
        if not text:
            return
        api_key = self.get_api_key()
        if not api_key:
            messagebox.showerror("错误", "请先填写 MiniMax API Key")
            return
        try:
            mp3_bytes = vc.synthesize_test(voice_id, text, api_key)
            tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            tmp.write(mp3_bytes)
            tmp.close()
            subprocess.Popen(["afplay", tmp.name])
        except Exception as e:
            messagebox.showerror("试听失败", str(e))

    def _delete_voice(self, voice_id):
        if not messagebox.askyesno("确认", f"删除音色 {voice_id}？"):
            return
        try:
            vl.delete(voice_id)
            self._refresh_list()
        except Exception as e:
            messagebox.showerror("删除失败", str(e))

    def set_enabled(self, enabled):
        """Engine-aware gray-out. Spec: keep panel visible, disable buttons + tooltip."""
        state = "normal" if enabled else "disabled"
        self.clone_btn.configure(state=state)
        for w in self.list_frame.winfo_children():
            try:
                w.configure(state=state)
            except Exception:
                pass
        # Tooltip — customtkinter doesn't have native tooltip; use bind + transient label
        if not enabled:
            self.clone_btn.bind(
                "<Enter>",
                lambda e: self._show_tooltip(LIBRARY_GRAYOUT_TOOLTIP),
            )
            self.clone_btn.bind("<Leave>", lambda e: self._hide_tooltip())
        else:
            self.clone_btn.unbind("<Enter>")
            self.clone_btn.unbind("<Leave>")
            self._hide_tooltip()

    def _show_tooltip(self, text):
        if hasattr(self, "_tooltip") and self._tooltip:
            return
        x = self.clone_btn.winfo_rootx() + 20
        y = self.clone_btn.winfo_rooty() + self.clone_btn.winfo_height() + 4
        self._tooltip = tk.Toplevel(self.winfo_toplevel())
        self._tooltip.wm_overrideredirect(True)
        self._tooltip.wm_geometry(f"+{x}+{y}")
        tk.Label(
            self._tooltip, text=text, background="#FFFBE6",
            relief="solid", borderwidth=1, padx=6, pady=3,
            font=("Helvetica Neue", 10), foreground=GHIBLI["text"],
        ).pack()

    def _hide_tooltip(self):
        if hasattr(self, "_tooltip") and self._tooltip:
            self._tooltip.destroy()
            self._tooltip = None


class _VoiceRow(ctk.CTkFrame):
    def __init__(self, parent, voice, on_play, on_delete):
        super().__init__(parent, fg_color=GHIBLI["white"], corner_radius=4)
        self._voice = voice
        self._on_play = on_play
        self._on_delete = on_delete
        self.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            self, text=f"🎤 {voice['display_name']}",
            font=ctk.CTkFont("Helvetica Neue", 11, "bold"),
            text_color=GHIBLI["text"],
        ).grid(row=0, column=0, padx=(8, 4), pady=4)

        ctk.CTkLabel(
            self, text=f"({voice['voice_id']})",
            font=ctk.CTkFont("Helvetica Neue", 10),
            text_color=GHIBLI["text_light"],
        ).grid(row=0, column=1, sticky="w", padx=(0, 4), pady=4)

        ctk.CTkButton(
            self, text="▶", width=32, height=24,
            fg_color=GHIBLI["grass"], hover_color=GHIBLI["forest"],
            text_color=GHIBLI["white"], corner_radius=4,
            command=lambda: self._on_play(voice["voice_id"]),
        ).grid(row=0, column=2, padx=2, pady=4)

        ctk.CTkButton(
            self, text="🗑", width=32, height=24,
            fg_color=GHIBLI["dust"], hover_color=GHIBLI["text_light"],
            text_color=GHIBLI["white"], corner_radius=4,
            command=lambda: self._on_delete(voice["voice_id"]),
        ).grid(row=0, column=3, padx=(2, 8), pady=4)


class CloneDialog(ctk.CTkToplevel):
    """Single-panel recording → preview → save flow with countdown."""

    def __init__(self, parent, api_key, on_success):
        super().__init__(parent.winfo_toplevel())
        self.api_key = api_key
        self.on_success = on_success
        self._recording = False
        self._wav_path = None
        self._countdown_job = None
        self._duration = DEFAULT_DURATION
        self.title("克隆新音色")
        self.geometry("560x620")
        self.grab_set()
        self._build_ui()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self, text="🎙️ 录制一段声音（10-60 秒）",
            font=ctk.CTkFont("Helvetica Neue", 14, "bold"),
            text_color=GHIBLI["text"],
        ).grid(row=0, column=0, padx=12, pady=(12, 4), sticky="w")

        # Duration selector
        dur_frame = ctk.CTkFrame(self, fg_color="transparent")
        dur_frame.grid(row=1, column=0, padx=12, pady=(0, 4), sticky="ew")
        ctk.CTkLabel(dur_frame, text="时长：", text_color=GHIBLI["text"]).pack(side="left")
        self.duration_var = ctk.IntVar(value=DEFAULT_DURATION)
        ctk.CTkOptionMenu(
            dur_frame, variable=self.duration_var, values=[str(d) for d in DURATION_OPTIONS],
            fg_color=GHIBLI["cream"], button_color=GHIBLI["grass"], width=80,
        ).pack(side="left", padx=(4, 0))

        # Sample text selector
        ctk.CTkLabel(self, text="示例文本：", text_color=GHIBLI["text"]).grid(
            row=2, column=0, padx=12, pady=(8, 2), sticky="w")
        self.sample_var = ctk.StringVar(value="通用")
        ctk.CTkOptionMenu(
            self, variable=self.sample_var, values=list(SAMPLE_TEXTS.keys()),
            fg_color=GHIBLI["cream"], button_color=GHIBLI["grass"],
        ).grid(row=3, column=0, padx=12, pady=(0, 4), sticky="ew")

        self.sample_text = ctk.CTkTextbox(self, height=70, fg_color=GHIBLI["cream"])
        self.sample_text.insert("0.0", SAMPLE_TEXTS["通用"])
        self.sample_text.grid(row=4, column=0, padx=12, pady=(0, 8), sticky="ew")
        self.sample_var.trace_add("write", lambda *_: self._on_sample_change())

        # Countdown display + progress bar
        self.countdown_var = ctk.StringVar(value="")
        ctk.CTkLabel(
            self, textvariable=self.countdown_var,
            font=ctk.CTkFont("Helvetica Neue", 28, "bold"),
            text_color=GHIBLI["totoro"],
        ).grid(row=5, column=0, padx=12, pady=(4, 2))

        self.progress = ctk.CTkProgressBar(self, fg_color=GHIBLI["cream"], progress_color=GHIBLI["grass"])
        self.progress.set(0)
        self.progress.grid(row=6, column=0, padx=12, pady=(0, 8), sticky="ew")

        # Record / preview buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=7, column=0, padx=12, pady=(0, 4), sticky="ew")
        btn_frame.grid_columnconfigure(0, weight=1)
        btn_frame.grid_columnconfigure(1, weight=1)

        self.rec_btn = ctk.CTkButton(
            btn_frame, text="⏺ 开始录制",
            fg_color=GHIBLI["totoro"], hover_color=GHIBLI["totoro_dark"],
            text_color=GHIBLI["white"], command=self._start_record,
        )
        self.rec_btn.grid(row=0, column=0, sticky="ew", padx=(0, 2))

        self.preview_btn = ctk.CTkButton(
            btn_frame, text="▶ 试听录制",
            fg_color=GHIBLI["cream"], hover_color=GHIBLI["dust"],
            text_color=GHIBLI["text"], command=self._preview_record,
            state="disabled",
        )
        self.preview_btn.grid(row=0, column=1, sticky="ew", padx=(2, 0))

        # Naming (two fields: display_name + voice_id, both editable)
        ctk.CTkLabel(self, text="显示名称（中文）：", text_color=GHIBLI["text"]).grid(
            row=8, column=0, padx=12, pady=(8, 2), sticky="w")
        self.name_var = ctk.StringVar(value="")
        ctk.CTkEntry(self, textvariable=self.name_var,
                     placeholder_text="例如：张医生的声音",
                     fg_color=GHIBLI["cream"]).grid(row=9, column=0, padx=12, pady=(0, 4), sticky="ew")

        ctk.CTkLabel(self, text="voice_id（ASCII，作为 API 标识）：", text_color=GHIBLI["text"]).grid(
            row=10, column=0, padx=12, pady=(4, 2), sticky="w")
        self.voice_id_var = ctk.StringVar(value="")
        ctk.CTkEntry(self, textvariable=self.voice_id_var,
                     placeholder_text="例如：dr_zhang",
                     fg_color=GHIBLI["cream"]).grid(row=11, column=0, padx=12, pady=(0, 4), sticky="ew")

        # Save button
        self.save_btn = ctk.CTkButton(
            self, text="💾 保存并上传",
            fg_color=GHIBLI["grass"], hover_color=GHIBLI["forest"],
            text_color=GHIBLI["white"], command=self._save,
        )
        self.save_btn.grid(row=12, column=0, padx=12, pady=(8, 4), sticky="ew")

        # Status label
        self.status_var = ctk.StringVar(value="准备就绪")
        ctk.CTkLabel(
            self, textvariable=self.status_var,
            text_color=GHIBLI["text_light"],
            font=ctk.CTkFont("Helvetica Neue", 11),
        ).grid(row=13, column=0, padx=12, pady=(0, 12), sticky="w")

    def _on_sample_change(self):
        key = self.sample_var.get()
        self.sample_text.delete("0.0", "end")
        self.sample_text.insert("0.0", SAMPLE_TEXTS.get(key, ""))

    def _start_record(self):
        if self._recording:
            return
        self._duration = self.duration_var.get()
        self._wav_path = None
        self.preview_btn.configure(state="disabled")
        self.rec_btn.configure(state="disabled", text="录制中...")
        self._recording = True
        self.progress.set(0)
        self._start_countdown()
        threading.Thread(target=self._do_record, daemon=True).start()

    def _start_countdown(self):
        """Tick once per second; updates big numeric + progress bar."""
        def tick():
            if not self._recording:
                return
            # Use elapsed-from-start approximation; recorded file's actual duration is decided in worker
            elapsed = getattr(self, "_elapsed", 0)
            remaining = max(0, self._duration - elapsed)
            self.countdown_var.set(f"{remaining}s")
            self.progress.set(elapsed / self._duration)
            self._elapsed = elapsed + 1
            self._countdown_job = self.after(1000, tick)

        self._elapsed = 0
        tick()

    def _stop_countdown(self):
        if self._countdown_job:
            self.after_cancel(self._countdown_job)
            self._countdown_job = None
        self.countdown_var.set("")
        self.progress.set(1.0)

    def _do_record(self):
        try:
            wav_path = vr.record(seconds=self._duration)
            self._wav_path = wav_path
            self.after(0, lambda: self.status_var.set("✅ 录制完成，请先试听再保存"))
            self.after(0, lambda: self.rec_btn.configure(state="normal", text="⏺ 重新录制"))
            self.after(0, lambda: self.preview_btn.configure(state="normal"))
        except Exception as e:
            self.after(0, lambda: self.status_var.set(f"❌ 录音失败: {e}"))
            self.after(0, lambda: self.rec_btn.configure(state="normal", text="⏺ 重试"))
        finally:
            self._recording = False
            self.after(0, self._stop_countdown)

    def _preview_record(self):
        """Spec: preview is REQUIRED before save."""
        if not self._wav_path:
            messagebox.showerror("未录制", "请先录音")
            return
        try:
            subprocess.Popen(["afplay", self._wav_path])
        except Exception as e:
            messagebox.showerror("试听失败", str(e))

    def _save(self):
        if not self._wav_path:
            messagebox.showerror("未录制", "请先录音")
            return
        name = self.name_var.get().strip()
        voice_id = self.voice_id_var.get().strip()
        if not name:
            messagebox.showerror("未命名", "请填写显示名称")
            return
        if not VOICE_ID_PATTERN.match(voice_id):
            messagebox.showerror(
                "voice_id 不合法",
                "voice_id 必须 3-32 字符，仅含 a-z A-Z 0-9 _ -",
            )
            return
        # Spec: preview required before save
        if not messagebox.askyesno("确认", "已经试听过了吗？没试听的话先点「▶ 试听录制」。继续保存？"):
            return
        existing = vl.exists(voice_id)
        if existing:
            choice = messagebox.askyesnocancel(
                "voice_id 已存在",
                f"voice_id `{voice_id}` 已在库中。\n"
                f"是=先克隆新音色，成功后再覆盖旧的（安全）\n"
                f"否=返回并改名\n"
                f"取消=放弃",
            )
            if not choice:  # No or Cancel
                return
            # Yes: continue with safe-overwrite flow (clone first, then swap)

        sample_text = self.sample_text.get("0.0", "end").strip()
        self.save_btn.configure(state="disabled", text="上传中...")
        self.status_var.set("⏫ 上传到 MiniMax...")
        threading.Thread(
            target=self._do_save,
            args=(voice_id, name, sample_text, existing),
            daemon=True,
        ).start()

    def _do_save(self, voice_id, name, sample_text, existing_was_there):
        """Safe-overwrite: clone first, only delete old on success."""
        try:
            vc.clone(self._wav_path, voice_id, sample_text, self.api_key)
        except Exception as e:
            self.after(0, lambda: self.status_var.set(f"❌ 克隆失败: {e}"))
            self.after(0, lambda: self.save_btn.configure(state="normal", text="💾 重试"))
            return
        try:
            if existing_was_there:
                vl.delete(voice_id)
            vl.add(voice_id, name, self._wav_path)
        except Exception as e:
            self.after(0, lambda: self.status_var.set(f"❌ 写库失败: {e}"))
            self.after(0, lambda: self.save_btn.configure(state="normal", text="💾 重试"))
            return
        self.after(0, lambda: self.status_var.set("✅ 克隆成功"))
        self.after(0, lambda: self.on_success())
        self.after(500, self.destroy)


class VoicePickerModal(ctk.CTkToplevel):
    """Modal opened from VoiceConfigRow when user picks '从音色库选'."""

    def __init__(self, parent, on_pick):
        super().__init__(parent.winfo_toplevel())
        self._on_pick = on_pick
        self._result = None
        self.title("从音色库选择")
        self.geometry("420x360")
        self.grab_set()
        self._build_ui()

    def _build_ui(self):
        ctk.CTkLabel(
            self, text="📚 选择音色",
            font=ctk.CTkFont("Helvetica Neue", 14, "bold"),
            text_color=GHIBLI["text"],
        ).pack(padx=12, pady=(12, 4), anchor="w")

        list_frame = ctk.CTkScrollableFrame(self, fg_color=GHIBLI["cream"])
        list_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        voices = vl.list()
        if not voices:
            ctk.CTkLabel(
                list_frame, text="（音色库为空）",
                text_color=GHIBLI["text_light"],
            ).pack(padx=10, pady=10)
            return
        for v in voices:
            row = ctk.CTkFrame(list_frame, fg_color=GHIBLI["white"], corner_radius=4)
            row.pack(fill="x", padx=4, pady=2)
            ctk.CTkLabel(
                row, text=f"🎤 {v['display_name']}  ({v['voice_id']})",
                font=ctk.CTkFont("Helvetica Neue", 11),
                text_color=GHIBLI["text"],
            ).pack(side="left", padx=8, pady=6)
            ctk.CTkButton(
                row, text="选用", width=60,
                fg_color=GHIBLI["grass"], hover_color=GHIBLI["forest"],
                text_color=GHIBLI["white"],
                command=lambda vid=v["voice_id"]: self._pick(vid),
            ).pack(side="right", padx=8, pady=6)

    def _pick(self, voice_id):
        self._result = voice_id
        self._on_pick(voice_id)
        self.destroy()

    def get_result(self):
        return self._result
```

- [ ] **Step 2: Sanity-import the module**

```bash
python -c "from gui.panels import voice_library_panel; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add gui/panels/voice_library_panel.py
git commit -m "feat(voice_library_panel): GUI panel + CloneDialog + VoicePickerModal

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 9: Integrate dropdown entry in `speakers.py`

**Files:**
- Modify: `gui/panels/speakers.py:25-48` (add to `MINIMAX_VOICES`)
- Modify: `gui/panels/speakers.py:158-160` (`_on_dropdown_change`)
- Modify: `gui/panels/speakers.py:175-180` (`VoiceConfigRow.get_voice_id`)

- [ ] **Step 1: Add the new dropdown entry to `MINIMAX_VOICES`**

In `gui/panels/speakers.py`, inside the `MINIMAX_VOICES` list (around line 25-48), append at the end (literal matches spec):

```python
    # 从用户音色库选择（最后一项；display 文本用于前缀匹配）
    ("__from_library__", "📚 从音色库选"),
```

- [ ] **Step 2: Update `_on_dropdown_change` so the library option does NOT clear `custom_var`**

Modify the existing method (around line 158):

```python
    def _on_dropdown_change(self, value):
        # 选了"复刻音色"或"从音色库选"项时，保留 custom_var（避免残留清空）
        if "复刻音色" not in value and "从音色库" not in value:
            self.custom_var.set("")
```

- [ ] **Step 3: Update `VoiceConfigRow.get_voice_id()` to handle the library option**

Modify the `get_voice_id` method (around line 175-180):

```python
    def get_voice_id(self):
        # 如果选了"从音色库选"，弹窗让用户选
        if self.voice_var.get().startswith("📚 从音色库"):
            from gui.panels.voice_library_panel import VoicePickerModal
            modal = VoicePickerModal(self.winfo_toplevel(), on_pick=self._on_library_pick)
            self.wait_window(modal)
            return modal.get_result() or self.custom_var.get().strip()
        custom = self.custom_var.get().strip()
        if custom:
            return custom
        display = self.voice_var.get()
        return next((k for k, v in MINIMAX_VOICES if v == display), MINIMAX_VOICES[0][0])

    def _on_library_pick(self, voice_id):
        """Callback: 把选中的 voice_id 同步到 custom_var。"""
        self.custom_var.set(voice_id)
```

- [ ] **Step 4: Verify the new entry is present and `get_voice_id` recognises it**

```bash
python -c "
from gui.panels.speakers import MINIMAX_VOICES, VoiceConfigRow
assert any(label.startswith('📚 从音色库') for _, label in MINIMAX_VOICES), 'dropdown entry missing'
# Display string used by get_voice_id().startswith() must match the one we added
target = [label for _, label in MINIMAX_VOICES if label.startswith('📚 从音色库')][0]
print(f'OK: library entry present, label={target!r}')
"
```
Expected: `OK: library entry present, label='📚 从音色库选'` (no trailing dots — matches spec)

- [ ] **Step 5: Commit**

```bash
git add gui/panels/speakers.py
git commit -m "feat(speakers): add '📚 从音色库选' dropdown entry

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 10: Integrate panel into `input.py` with engine gray-out

**Files:**
- Modify: `gui/panels/input.py`

**Current `InputPanel` layout** (from reading the file):
- Row 0: title label
- Row 1: `cred` (CredentialRow)
- Row 2: `tabs` (CTkTabview)
- Row 3: `speaker_row` ↔ `minimax_voice_list` (overlaid, engine-switched)
- Row 4: `intro_outro_row`
- Row 5: `hint_label` (variable row)
- Row 6/7: `gen_btn` (variable row, engine-switched)

**Strategy:** Insert `voice_library_panel` at row 2, pushing everything below down by 1. Engine switch no longer needs to vary row numbers because the library panel is always visible (gray-out only).

- [ ] **Step 1: Add import**

At the top of `gui/panels/input.py`, after the existing `from gui.panels.speakers import SpeakerRow, MiniMaxVoiceList` line:

```python
from gui.panels.voice_library_panel import VoiceLibraryPanel
```

- [ ] **Step 2: Insert the panel between cred and tabs**

After the credentials block (`cred.grid(row=1, ...)`, `self.cred = cred`, the `cred.engine_var.trace_add(...)` line), add:

```python
# 音色库面板：始终可见，豆包引擎下灰显 + tooltip
self.voice_library_panel = VoiceLibraryPanel(
    self,
    get_engine=self.cred.get_engine,
    get_api_key=self.cred.get_minimax_key,
    fg_color=GHIBLI["cream_dark"],
)
self.voice_library_panel.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 8))
```

- [ ] **Step 3: Shift existing rows down by 1**

Update each `grid(row=N, ...)` to `grid(row=N+1, ...)` for the rows below the new panel:
- `self.tabs.grid(row=3, ...)`
- `self.speaker_row.grid(row=4, ...)`
- `self.minimax_voice_list.grid(row=4, ...)` (line 55)
- `self.intro_outro_row.grid(row=5, ...)`
- `self.hint_label.grid(row=6, ...)`
- `self.gen_btn.grid(row=7, ...)`

Also update the initial `self.grid_rowconfigure(5, weight=1)` to `self.grid_rowconfigure(6, weight=1)` (the row with `weight=1` is hint_label which moved from 5 to 6).

- [ ] **Step 4: Simplify `_on_engine_change` (no more variable row numbers)**

Replace the body of `_on_engine_change` (lines 135-147):

```python
    def _on_engine_change(self):
        """切换引擎：更新音色面板显隐 + 库面板灰显。"""
        engine = self.cred.get_engine()
        if engine == "minimax":
            self.speaker_row.grid_remove()
            self.minimax_voice_list.grid()
        else:
            self.speaker_row.grid()
            self.minimax_voice_list.grid_remove()
        # 音色库面板：始终可见，按引擎灰显
        self.voice_library_panel.set_enabled(engine == "minimax")
```

(Row numbers are now stable — voice_library_panel is always at row 2, hint_label always at row 6, gen_btn always at row 7. No more `self.hint_label.grid(row=6)` / `self.gen_btn.grid(row=7)` switching inside this method.)

- [ ] **Step 5: Verify layout by launching the GUI and toggling engines**

```bash
PYTHONPATH=. python3 gui/main_window.py &
PID=$!
sleep 3
# If no error printed to stderr in those 3s, layout is sound.
kill $PID 2>/dev/null || true
```

Expected: window opens, no traceback. Toggle between 豆包 / MiniMax and confirm the library panel stays visible but its controls disable.

- [ ] **Step 6: Commit**

```bash
git add gui/panels/input.py
git commit -m "feat(input): integrate voice_library_panel with stable layout

Shifts existing rows down by 1; library panel stays visible
across engines (gray-out + tooltip on Doubao per spec).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 11: Manual smoke test + polish

**Files:** None (verification task)

- [ ] **Step 1: Launch the GUI**

```bash
PYTHONPATH=. python3 gui/main_window.py
```

Expected: window opens without error. No new exception in stderr.

- [ ] **Step 2: Verify gray-out on Doubao (per spec: panel stays visible, controls disabled)**

- Select 豆包 engine.
- **Confirm**: voice library panel is **still visible** (NOT hidden).
- **Confirm**: "🎙️ 克隆新音色" button is disabled (grayed out).
- **Confirm**: hovering the disabled button shows the tooltip "豆包引擎不支持音色克隆，请切换到 MiniMax".
- Switch to MiniMax. Confirm the same panel re-enables (button is no longer gray, tooltip no longer fires).

- Switch to MiniMax, fill API key.
- Open voice library panel, click "🎙️ 克隆新音色".
- Grant mic permission if prompted.
- Pick "通用" sample text, click record, wait 30 sec.
- Confirm "录制完成" appears.
- Name the voice, click save.
- Confirm clone API succeeds and voice appears in the library list.

- [ ] **Step 4: Play back the cloned voice**

- Click ▶ on the newly added voice row.
- Confirm test synthesis plays via afplay.

- [ ] **Step 5: Use the cloned voice in a podcast**

- In the speakers panel, select "📚 从音色库选" in the dropdown.
- Pick the voice you just cloned.
- Run a quick podcast synthesis (short script, 2 speakers).
- Listen to the output — confirm it sounds like the cloned voice.

- [ ] **Step 6: Run full test suite one more time**

```bash
pytest
```
Expected: all tests pass.

- [ ] **Step 7: Commit any final touches (tooltips, message text, etc.)**

**WARNING:** Do NOT use `git add -A` — the parent repo root is `/Users/yirongcao`, so `-A` would stage hundreds of untracked dotfiles (`~/.ssh/`, `~/.aws/`, shell histories). Use explicit paths only:

```bash
git status                              # review what's about to be staged
git add <explicit-path-1> <explicit-path-2> ...
git commit -m "polish: final touches after manual smoke test

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

If only files in `Documents/podcast-generator/` need committing:

```bash
git add Documents/podcast-generator/
git commit -m "polish: final touches after manual smoke test

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Risk Register

| Risk | Mitigation |
|------|------------|
| **MiniMax clone endpoint URL / request shape differs from plan assumptions** | Task 1 verifies this BEFORE any code that calls the API is written. Endpoint URL is filled into `voice_cloner.py` from Task 1's finding. |
| **sounddevice fails to install on macOS without portaudio** | Task 3 Step 1 catches this and instructs `brew install portaudio`. |
| **macOS mic permission prompt blocks the user unexpectedly** | Documented in Task 11 smoke test. The user already grants mic perms for other apps; behavior should match. |
| **voice_id namespace pollution if MiniMax has no delete endpoint** | Task 1 Step 3 investigates; spec notes the constraint. UI suggests prefixing user voice_ids (e.g., `dr_`, `nurse_`) to avoid collisions with others. |
| **Existing `_atomic_write_json` is private** | Task 4 Step 3 copies the pattern; credentials.py is not touched. Future refactor could promote it. |
| **GUI tests not automated** | Plan relies on manual smoke test (Task 11). Acceptable trade-off for v1 given scope; future plan could add pytest-qt or similar. |
| **Test for `synthesize_test` mocks `minimax_tts.synthesize`** | Test is in `test_voice_cloner.py` and patches at the import site. If `minimax_tts` API changes, both code and test need updates — acceptable coupling. |

---

## Verification checklist (run before declaring done)

- [ ] `pytest` exits 0 with all tests passing
- [ ] GUI launches without error
- [ ] Clone flow works end-to-end (record → save → library)
- [ ] Voice playback via `afplay` works
- [ ] Voice picker from `speakers.py` works in a real podcast synthesis
- [ ] Doubao engine gray-out works
- [ ] All commits are atomic and well-described