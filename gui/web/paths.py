"""
paths.py — Shared path constants for the web module.

Single source of truth for filesystem locations so other modules don't
reach across with fragile `Path(__file__).resolve().parent.parent.parent`
chains. ROOT_DIR is the project root (where `podcast-generator` lives).

Usage:
    from gui.web.paths import ROOT_DIR, STATIC_DIR, SESSION_SECRET_FILE
    CONFIG_PATH = ROOT_DIR / "config.json"
"""
from __future__ import annotations

from pathlib import Path

# gui/web/paths.py → podcast-generator/
#   parents[0] = gui/web/
#   parents[1] = gui/
#   parents[2] = podcast-generator/  ← project root
ROOT_DIR = Path(__file__).resolve().parents[2]

# gui/web/paths.py → gui/web/
WEB_DIR = Path(__file__).resolve().parent

# Static assets (CSS / JS / HTML)
STATIC_DIR = WEB_DIR / "static"

# Default DB location (single-user → multi-user SQLite; later via env)
DB_PATH_DEFAULT = Path.home() / ".podcast-automation" / "podcast.db"

# Session-signing secret file (auto-generated on first run)
SESSION_SECRET_FILE = Path.home() / ".podcast-automation" / "session-secret"