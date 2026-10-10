# Voice Cloning & Custom Voice Library Design

**Status:** Draft (pending spec review)
**Date:** 2026-08-14
**Author:** Claude Code (brainstorming session)

## Problem

The current podcast generator (豆包 / MiniMax engines) only ships with preset voices. The user is a doctor who needs to record real human voices (themselves, colleagues, guests for patient-education content) and reuse them across podcasts. There is no UI to capture a voice, no library to manage multiple captured voices, and the existing `复刻音色（输入ID）` entry forces the user to obtain a voice_id out-of-band.

## Goals

1. **Capture a voice from microphone** → upload to MiniMax → save as a reusable voice profile.
2. **Persist a multi-voice library** across sessions and projects.
3. **Use library voices in podcast synthesis** without manually pasting voice_ids.

## Non-Goals (v1)

- Real-time waveform during recording.
- Automatic silence trimming.
- Cloud-shared / multi-user libraries.
- Voice-similarity scoring / auto-re-record suggestions.
- Re-cloning or updating an existing library voice (delete-and-re-add instead).

## Approach (chosen)

**Plan A — minimal viable.** Direct mic capture (`sounddevice`), WAV persistence on disk, MiniMax clone API call, JSON-indexed user-global library. No waveform, no auto-trim, no heavy deps.

## Constraints

- Voice cloning API is MiniMax-only. 豆包 does not support cloning.
- Library storage is **user-global** (`~/Library/Application Support/PodcastGenerator/voices/`).
- 豆包 engine selection must not break the library UI (gray-out + tooltip instead of hide).
- Must integrate with the existing dynamic voice rows in `speakers.py` without breaking current behavior.

## Architecture

### Design principles

- **Isolation**: recording / cloning API / library management / UI are separate units with clear interfaces; each can be unit-tested without the GUI.
- **Minimal intrusion**: existing `speakers.py` changes are limited to one new dropdown entry.
- **macOS-native paths**: use `~/Library/Application Support/PodcastGenerator/` for user data.

### New files

```
documents/podcast-generator/
├── voice_library.py             # CRUD + JSON persistence + atomic writes
├── voice_cloner.py              # MiniMax clone API client (separate from TTS)
├── voice_recorder.py            # sounddevice + soundfile mic capture wrapper
└── gui/panels/
    └── voice_library_panel.py   # Clone UI + library list (single panel)

~/Library/Application Support/PodcastGenerator/voices/
├── voices.json                  # Index: voice_id, display_name, wav_path, created_at, notes
└── audio/
    └── {voice_id}.wav           # Original recording per voice
```

### Modified files

| File | Change |
|------|--------|
| `gui/panels/speakers.py` | Add `("__from_library__", "📚 从音色库选")` to `MINIMAX_VOICES`. `VoiceConfigRow.get_voice_id()` detects this option and opens a `VoicePickerModal`. |
| `gui/panels/input.py` | Insert `voice_library_panel` below credentials panel (collapsed by default). |
| `exceptions.py` | Add `VoiceCloneError`, `LibraryError`. |
| `requirements.txt` (if present) | Add `sounddevice`, `soundfile`. |

## Component responsibilities

| Module | Purpose | Dependencies |
|--------|---------|--------------|
| `voice_recorder.py` | Capture microphone audio to a WAV file. Input: duration (sec), sample rate. Output: file path or bytes. | `sounddevice`, `soundfile`, `numpy` |
| `voice_cloner.py` | Call MiniMax clone API. Input: wav_path, voice_id, sample_text, api_key. Output: success / raise `VoiceCloneError`. | `requests` |
| `voice_library.py` | CRUD over voices, atomic JSON persistence, file copy to user-global dir. | stdlib only |
| `gui/panels/voice_library_panel.py` | Glues recorder + cloner + library; the only GUI-touching layer. | `customtkinter`, all three above |

## Data flow

### Clone new voice

```
sounddevice InputStream
      ↓ callback captures frames into numpy buffer
voice_recorder.record(seconds=30)
      ↓
/tmp/recording.wav  (16kHz mono 16-bit WAV)
      ↓ user listens, names, clicks Save
voice_cloner.clone(wav_path, voice_id, sample_text, api_key)
      ↓ POST https://api.minimaxi.com/v1/voice_clone  (multipart)
      ↓ on success: return voice_id
voice_library.add(voice_id, display_name, wav_path, notes)
      ├─ copy wav to ~/Library/.../audio/{voice_id}.wav
      └─ append entry to voices.json  (atomic write, thread-locked)
voice_library_panel: refresh list, show "克隆成功"
voice_cloner.synthesize_test(voice_id, text="测试成功")
      ↓ mp3 bytes
panel audio player plays confirmation
```

### Use library voice in speakers panel

```
VoiceConfigRow.get_voice_id()
      ↓ detect dropdown == "__from_library__"
VoicePickerModal.show(parent)
      ├─ reads voice_library.list()
      ├─ user clicks a voice row
      └─ returns selected voice_id
self.custom_var.set(selected_voice_id)
      ↓
return self.custom_var.get()
```

The dropdown's existing `command=self._on_dropdown_change` already clears `custom_var` when a non-library option is selected. No additional state needed.

### Listen to a library voice

```
VoiceLibraryPanel: user clicks ▶ on a row
      ↓ prompt for test text (default: "这是一个测试")
voice_cloner.synthesize_test(voice_id, text)
      ↓ uses existing MiniMax TTS path (or synthesize_segments for short text)
      ↓ writes mp3 to /tmp/voice_test.mp3
panel plays via subprocess.Popen(['afplay', '/tmp/voice_test.mp3'])
      ↑ macOS-native; no extra dependency
```

(Decision: in v1, test playback uses existing `minimax_tts.synthesize()` to avoid duplicating the TTS client. The voice_id is just a string. Playback uses `afplay` so no new audio dep is needed beyond what's already used for synthesis.)

## UX details

| Dimension | Decision | Rationale |
|-----------|----------|-----------|
| Recording format | WAV 16kHz mono 16-bit | Standard for voice cloning APIs |
| Min recording | 10 sec (enforced client-side) | Shorter recordings clone poorly |
| Max recording | 60 sec (enforced client-side) | API limit; longer is wasteful |
| Countdown UI | Big numeric + progress bar | User confidence |
| Post-record preview | **Required**, no skip | Skipping = high fail rate |
| Sample reading text | 3 presets (medical / generic / slow-clear) + freeform | Presets guarantee quality |
| Naming | Chinese `display_name` + ASCII `voice_id` (user-editable, validated) | voice_id must be unique + safe |
| Post-clone verification | Auto-synthesize "测试成功" and play | Immediate confidence |
| Engine = MiniMax | Full UI accessible | Default path |
| Engine = 豆包 | Entire panel grayed-out + tooltip "豆包引擎不支持音色克隆，请切换到 MiniMax" | User's chosen fallback |

## Error handling

| Category | Trigger | User sees | System response |
|----------|---------|-----------|-----------------|
| Recording failure | Mic permission denied, device busy, sample error | Error dialog; temp file deleted | `VoiceRecorderError`, caught in panel |
| Recording too short | < 10 sec | Red inline message "再录长点" | Frontend gate; no backend call |
| Clone API failure | Network, auth, rate limit, bad audio | Error dialog; keep temp file for retry | `VoiceCloneError(status, msg)`, surface raw message |
| voice_id conflict | Library already has same id | Dialog "已存在，覆盖/改名/取消" | `voice_library.exists()` check, 3-way choice |
| Library file corrupt | voices.json invalid JSON | Dialog "库文件已损坏，已备份到 voices.json.bak，新建空库" | `voice_library.load()` try/except, atomic backup |
| App support dir unwritable | Permission / disk full | Dialog "无法写入用户目录: <path>" | Catch `PermissionError`/`OSError`, never silent |
| Voice no longer valid on MiniMax | Library has id but API returns 404 on synthesis | Synthesis fails with existing API error path | No library-side cache invalidation in v1; future "健康检查" button |

## Threading & concurrency

- **Recording doesn't block main thread**: `sounddevice` runs in its own stream; main thread polls progress via `after()`.
- **Upload doesn't block UI**: runs in `threading.Thread`; button disabled with progress indicator.
- **Library file writes**: re-use the atomic-write pattern from `credentials.py:_atomic_write_json`. Since that helper is underscore-prefixed (private), the implementation plan must either (a) promote it to a shared utility module, or (b) move a copy into `voice_library.py`. Guarded by `threading.Lock`.

## Testing strategy

### Unit tests (no GUI)

```
test_voice_library.py
  ├─ test_add_list_delete_voice
  ├─ test_voice_id_conflict_detection
  ├─ test_atomic_write_no_corruption_on_crash
  ├─ test_corrupt_json_recovers_to_backup
  └─ test_concurrent_writes_thread_safe

test_voice_cloner.py
  ├─ test_clone_success_200  (mock HTTP)
  ├─ test_clone_400_invalid_audio
  ├─ test_clone_401_auth_failure
  └─ test_clone_429_rate_limit_with_retry

test_voice_recorder.py
  ├─ test_record_produces_valid_wav  (mock sounddevice)
  ├─ test_record_respects_duration_limit
  └─ test_record_cleans_temp_on_error
```

### Integration test

```
test_voice_picker_integration.py
  └─ test_full_clone_then_pick_then_synthesize  (all externals mocked)
```

### Manual GUI tests

- Recording flow: mic permission prompt, countdown, preview, re-record.
- Library panel: CRUD, preview, tooltip.
- Speakers.py integration: select library voice → run a podcast → confirm output is the target voice.

### Not tested

- Real MiniMax API behavior (CI can't reach it; gated by account/quota).
- Microphone hardware (environment-dependent).
- Third-party libraries (`sounddevice`, `soundfile`).

## File-by-file change list (for implementation plan)

1. `exceptions.py` — add `VoiceCloneError(status_code, message)`, `LibraryError(message)`.
2. `voice_recorder.py` — new file. Class `VoiceRecorder` with `record(seconds, sample_rate=16000) -> Path`. Raises `VoiceRecorderError`.
3. `voice_cloner.py` — new file. Class `VoiceCloner` with `clone(wav_path, voice_id, sample_text, api_key) -> None` and `synthesize_test(voice_id, text, api_key) -> bytes`. Raises `VoiceCloneError`.
4. `voice_library.py` — new file. Functions: `init()`, `list()`, `get(voice_id)`, `add(voice_id, display_name, wav_path, notes)`, `delete(voice_id)`, `update(voice_id, **fields)`, `exists(voice_id)`. Thread-safe atomic JSON writes. Backs up corrupt files to `voices.json.bak`. **Scope note:** `update()` covers `display_name` and `notes` only — re-cloning a voice is out of scope (delete + add instead).
5. `gui/panels/voice_library_panel.py` — new file. Class `VoiceLibraryPanel(ctk.CTkFrame)` with: "克隆新音色" button → recording dialog; scrollable library list with per-row preview/delete/rename buttons; engine-aware gray-out.
6. `gui/panels/speakers.py` — add `("__from_library__", "📚 从音色库选")` to `MINIMAX_VOICES`; `VoiceConfigRow.get_voice_id()` opens `VoicePickerModal` when this option is selected. Modal lives in `voice_library_panel.py` to avoid circular import.
7. `gui/panels/input.py` — instantiate `voice_library_panel` and grid below credentials.
8. `requirements.txt` — add `sounddevice`, `soundfile`.

## Open questions

None at design time. Implementation may surface questions about the exact MiniMax clone endpoint and request shape; will be verified during the implementation phase.