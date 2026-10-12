"""
auth.py — bcrypt + session + dependency injection

Layered:
    SessionMiddleware (signed cookie, secret from SESSION_SECRET env or
    auto-generated file) → request.session['user_id'] = int
    → get_current_user() reads it

Session secret strategy:
    Priority order:
    1. SESSION_SECRET env var (explicit, for prod)
    2. ~/.podcast-automation/session-secret file (auto-generated on first run)
    3. Auto-generate, write to file with 0o600 perms, return

    Never fall back to a hard-coded default — that would let attackers
    forge admin cookies. The dev fallback that previously existed has
    been removed.

Max age = 7 days
"""
from __future__ import annotations

import json
import os
import secrets as _secrets
from pathlib import Path
from typing import Optional
import bcrypt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from gui.web.db import get_db
from gui.web.models import User


SESSION_SECRET_FILE = Path.home() / ".podcast-automation" / "session-secret"
SESSION_MAX_AGE = 7 * 24 * 60 * 60  # 7 days
BCRYPT_ROUNDS = 12  # NIST-style guidance: ≥ 12 for interactive auth


def _resolve_session_secret() -> str:
    """Resolve session signing secret.

    Order:
      1. SESSION_SECRET env var
      2. Read from SESSION_SECRET_FILE (auto-generated on first run)
      3. Auto-generate, write to file (0o600), return

    Never falls back to a hard-coded value — that would let attackers
    forge admin cookies for any user_id.
    """
    env_secret = os.environ.get("SESSION_SECRET")
    if env_secret:
        return env_secret

    # Try to read existing file
    if SESSION_SECRET_FILE.exists():
        stored = SESSION_SECRET_FILE.read_text().strip()
        if stored and len(stored) >= 32:
            return stored
        # File is corrupted/empty — fall through to regenerate

    # Generate and persist
    SESSION_SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    new_secret = _secrets.token_urlsafe(48)  # 384 bits of entropy
    SESSION_SECRET_FILE.write_text(new_secret)
    SESSION_SECRET_FILE.chmod(0o600)  # owner-only
    return new_secret


SESSION_SECRET = _resolve_session_secret()


def hash_password(plain: str) -> str:
    """bcrypt hash. Cost=BCRYPT_ROUNDS (default 12)."""
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time compare."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def get_current_user_optional(
    request: Request,
    db: Session = Depends(get_db),
) -> Optional[User]:
    """Returns User if logged in, else None. Use for routes with mixed auth."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.get(User, user_id)
    if not user or not user.is_active:
        return None
    return user


def get_current_user(
    user: Optional[User] = Depends(get_current_user_optional),
) -> User:
    """Returns User if logged in, else 401. Use for protected endpoints."""
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Session"},
        )
    return user


def require_admin(
    user: User = Depends(get_current_user),
) -> User:
    """Returns User if admin, else 403."""
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin only",
        )
    return user


def audit(event_type: str, user_id: Optional[int] = None, request: Optional[Request] = None,
          resource_type: Optional[str] = None, resource_id: Optional[int] = None,
          metadata: Optional[dict] = None) -> None:
    """Append to audit_log. Don't fail caller on audit error."""
    from gui.web.models import AuditLog
    from gui.web.db import SessionLocal
    db = SessionLocal()
    try:
        entry = AuditLog(
            user_id=user_id,
            event_type=event_type,
            resource_type=resource_type,
            resource_id=resource_id,
            ip_address=request.client.host if request and request.client else None,
            user_agent=request.headers.get("user-agent") if request else None,
            metadata_json=json.dumps(metadata, ensure_ascii=False) if metadata else None,
        )
        db.add(entry)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()