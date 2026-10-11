"""
db.py — SQLAlchemy 2.x setup + get_db dependency

DB location: ~/.podcast-automation/podcast.db (single SQLite file)
Why SQLite: single-user → multi-user transition; later PostgreSQL via DATABASE_URL env.

Layered architecture:
    db.py       ← engine, SessionLocal, Base, get_db() (this file)
    models.py   ← ORM models (User, Article, Script, ...)
    migrations/ ← Alembic versions

FastAPI usage:
    @app.get("/api/...")
    def endpoint(db: Session = Depends(get_db), user = Depends(get_current_user)):
        ...
"""
from __future__ import annotations

import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session

DB_PATH = Path.home() / ".podcast-automation" / "podcast.db"
DATABASE_URL = os.environ.get("PODCAST_DATABASE_URL", f"sqlite:///{DB_PATH}")

# SQLite needs check_same_thread=False for FastAPI's thread pool
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, future=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db() -> Session:
    """FastAPI dependency: yields a session, ensures close."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables. Called at app startup if no migrations exist."""
    from gui.web import models  # noqa: F401  ensure models registered
    Base.metadata.create_all(bind=engine)