"""
migrate_legacy.py — One-shot migration of legacy JSON config into DB.

Run via:
    ~/.podcast-automation/.venv/bin/python -m gui.web.migrate_legacy

What it does:
  - Reads ~/Library/Application Support/PodcastGenerator/voices/voices.json
    → voice_clones table
  - Reads ~/Library/Application Support/PodcastGenerator/scripts/script_*.json
    → scripts table
  - Assigns owner_id = first user in DB (creates admin if no users exist)
  - Skips tables that already have rows (idempotent)
  - Leaves legacy JSON files on disk as backup (does NOT delete them)

Idempotent: safe to run multiple times. Re-running after a successful
migration is a no-op (counts skip with "already exist" message).
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from gui.web.auth import get_default_owner
from gui.web.db import SessionLocal, init_db
from gui.web.models import User, VoiceClone, Script

log = logging.getLogger(__name__)

# Legacy paths (PySide6 时代的 storage)
LEGACY_VOICES_JSON = Path.home() / "Library" / "Application Support" / "PodcastGenerator" / "voices" / "voices.json"
LEGACY_SCRIPTS_DIR = Path.home() / "Library" / "Application Support" / "PodcastGenerator" / "scripts"


def _parse_dt(s: str | None) -> Optional[datetime]:
    """Parse ISO 8601 / RFC 3339 string. Returns None on failure."""
    if not s:
        return None
    try:
        # voices.json uses "+00:00" or "Z" suffix
        s = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except (ValueError, AttributeError):
        return None


def _get_or_bootstrap_owner(db) -> User:
    """
    Find the legacy owner. Order:
      1. auth.get_default_owner (admin first, then first user)
      2. Bootstrap: create a 'legacy_owner' admin user (no password —
         meant for first-run DB seeding; user can later create real
         accounts via /api/auth/register).

    Returns: User instance (already in session).
    """
    try:
        return get_default_owner(db)
    except RuntimeError:
        pass

    # Bootstrap path — first run with empty DB
    log.warning("⚠  No users in DB. Bootstrapping legacy_owner account.")
    log.warning("   Register a real user via /api/auth/register and re-run.")
    owner = User(
        username="legacy_owner",
        password_hash="!bootstrap-no-password!",  # unusable, blocks login
        display_name="Legacy Owner (bootstrap)",
        is_admin=True,
    )
    db.add(owner)
    db.commit()
    db.refresh(owner)
    return owner


# ============================================================
# Voices migration
# ============================================================
def migrate_voices(db, owner: User) -> int:
    """
    Import voices.json → voice_clones. Returns number of new rows imported.

    Idempotency:
      - If owner already has voice_clones rows, skip entirely
      - If a voice_id already exists for this owner, skip that entry
        (so partial migrations can be resumed safely)
    """
    existing_count = db.query(VoiceClone).filter(VoiceClone.owner_id == owner.id).count()
    if existing_count > 0:
        log.info(f"  ⏭  voice_clones already populated ({existing_count} rows for owner), skipping")
        return 0

    if not LEGACY_VOICES_JSON.exists():
        log.info(f"  ⏭  voices.json not found: {LEGACY_VOICES_JSON}")
        return 0

    try:
        data = json.loads(LEGACY_VOICES_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"  ⚠  voices.json unreadable ({e}), skipping")
        return 0

    voices = data.get("voices", [])
    if not isinstance(voices, list):
        log.warning(f"  ⚠  voices.json 'voices' field is not a list: {type(voices).__name__}")
        return 0

    n = 0
    skipped_dup = 0
    for entry in voices:
        if not isinstance(entry, dict):
            continue
        voice_id = entry.get("voice_id")
        if not voice_id or not isinstance(voice_id, str):
            log.warning(f"  ⚠  skipping entry with no voice_id: {entry}")
            continue

        # Per-entry idempotency
        existing = (
            db.query(VoiceClone)
            .filter(VoiceClone.owner_id == owner.id, VoiceClone.voice_id == voice_id)
            .first()
        )
        if existing:
            skipped_dup += 1
            continue

        # audio_path: prefer wav_path (本地录音); fallback preview_path (voice_design)
        audio_path = entry.get("wav_path") or entry.get("preview_path")

        v = VoiceClone(
            owner_id=owner.id,
            voice_id=voice_id,
            display_name=entry.get("display_name") or voice_id,
            ref_text=entry.get("ref_text"),
            notes=entry.get("notes"),
            tags=list(entry.get("tags") or []),
            audio_path=audio_path,
            created_at=_parse_dt(entry.get("created_at")),
        )
        db.add(v)
        n += 1

    db.commit()
    log.info(
        f"  ✅ imported {n} voices"
        + (f" ({skipped_dup} duplicates skipped)" if skipped_dup else "")
    )
    return n


# ============================================================
# Scripts migration
# ============================================================
def migrate_scripts(db, owner: User) -> int:
    """
    Import scripts/*.json → scripts. Returns number of new rows imported.

    Idempotency:
      - Per-file: skip if same (owner, original_filename=stem) already
        imported (handles re-runs / partial imports).
      - original_filename is the file stem (e.g. "script_20261011_145102_topic")
        which is guaranteed unique within LEGACY_SCRIPTS_DIR.
      - Old dedup tuple was (owner, topic, created_at) which could collide
        if two legacy files happened to share metadata; original_filename
        is more reliable.
    """
    if not LEGACY_SCRIPTS_DIR.exists():
        log.info(f"  ⏭  scripts dir not found: {LEGACY_SCRIPTS_DIR}")
        return 0

    files = sorted(LEGACY_SCRIPTS_DIR.glob("script_*.json"))
    if not files:
        log.info(f"  ⏭  no script_*.json files in {LEGACY_SCRIPTS_DIR}")
        return 0

    n = 0
    skipped_dup = 0
    skipped_err = 0
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            log.warning(f"  ⚠  {path.name}: {e}, skipping")
            skipped_err += 1
            continue

        if not isinstance(data, dict):
            log.warning(f"  ⚠  {path.name}: not a JSON object, skipping")
            skipped_err += 1
            continue

        topic = (data.get("topic") or "未命名脚本").strip()
        if not topic:
            topic = "未命名脚本"

        segments = data.get("segments") or []
        if not isinstance(segments, list):
            segments = []

        editor_text = data.get("editor_text") or ""
        if not editor_text and segments:
            # Reconstruct from segments（legacy save 可能没存）
            try:
                from gui.web.api.script_gen import segments_to_editor_text
                editor_text = segments_to_editor_text(segments)
            except Exception:
                editor_text = ""

        created_at = _parse_dt(data.get("created_at")) or datetime.now()

        # Idempotency: original_filename (file stem) is unique on disk.
        original_filename = path.stem
        existing = (
            db.query(Script)
            .filter(
                Script.owner_id == owner.id,
                Script.original_filename == original_filename,
            )
            .first()
        )
        if existing:
            skipped_dup += 1
            continue

        script = Script(
            owner_id=owner.id,
            topic=topic,
            editor_text=editor_text,
            segments_json=json.dumps(segments, ensure_ascii=False) if segments else None,
            n_segments=sum(1 for s in segments if isinstance(s, dict) and (s.get("text") or "").strip()),
            original_filename=original_filename,
            created_at=created_at,
        )
        db.add(script)
        n += 1

    db.commit()
    log.info(
        f"  ✅ imported {n} scripts"
        + (f" ({skipped_dup} duplicates skipped)" if skipped_dup else "")
        + (f" ({skipped_err} errors)" if skipped_err else "")
    )
    return n


# ============================================================
# Main
# ============================================================
def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print("=" * 64)
    print("Legacy JSON → DB migration (v0.4.0-web Task 3)")
    print("=" * 64)

    init_db()
    db = SessionLocal()
    try:
        owner = _get_or_bootstrap_owner(db)
        print()
        print(f"Legacy owner: {owner.username} (id={owner.id}, admin={owner.is_admin})")
        print()

        print("→ Migrating voices...")
        n_voices = migrate_voices(db, owner)
        print()
        print("→ Migrating scripts...")
        n_scripts = migrate_scripts(db, owner)
        print()
        print("=" * 64)
        print(f"Done. Imported {n_voices} voices, {n_scripts} scripts.")
        print("Legacy JSON files left in place as backup (not deleted).")
        print("=" * 64)
        return 0
    except Exception as e:
        import traceback
        print()
        print("=" * 64)
        print(f"❌ Migration failed: {e}")
        traceback.print_exc()
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())