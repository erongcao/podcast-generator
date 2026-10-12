"""
test_db.py — DB layer tests (model relationships, cascade, soft delete)

Uses owner_id ForeignKey directly (Article/Synthesis don't have a `owner`
relationship in models.py — only `owner_id` column). Enables SQLite
PRAGMA foreign_keys=ON so CASCADE / SET NULL actually fire.
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from gui.web.db import Base
from gui.web.models import User, Article, Script, Synthesis, Output, VoiceClone, AuditLog


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    # SQLite needs PRAGMA foreign_keys=ON per connection for FK cascades to fire.
    # Without this, ondelete="CASCADE" is silently ignored.
    @event.listens_for(eng, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(eng)
    SessionLocal = sessionmaker(bind=eng)
    s = SessionLocal()
    yield s
    s.close()
    eng.dispose()


def test_user_password_hash_stored(session):
    """User model stores bcrypt hash; not exposed by Pydantic UserPublic schema."""
    u = User(username="alice", password_hash="$2b$12$abcdef...")
    session.add(u)
    session.commit()
    # The ORM model still has it (introspection)
    assert u.password_hash.startswith("$2b$12$")


def test_article_owner_cascade_delete(session):
    """Deleting owner cascades to articles."""
    u = User(username="alice", password_hash="x")
    session.add(u)
    session.flush()
    a = Article(owner_id=u.id, title="t", source_type="url", source_url="http://x", body="b")
    session.add(a)
    session.commit()
    aid = a.id

    session.delete(u)
    session.commit()

    assert session.get(Article, aid) is None


def test_script_article_set_null_on_delete(session):
    """Deleting article sets script.article_id to NULL (preserve script)."""
    u = User(username="alice", password_hash="x")
    session.add(u)
    session.flush()
    a = Article(owner_id=u.id, title="t", source_type="paste", source_url=None, body="b")
    session.add(a)
    session.flush()
    s = Script(owner_id=u.id, article_id=a.id, topic="topic", editor_text="hello")
    session.add(s)
    session.commit()
    sid = s.id

    session.delete(a)
    session.commit()

    s_after = session.get(Script, sid)
    assert s_after is not None  # script preserved
    assert s_after.article_id is None  # but article_id cleared


def test_synthesis_owner_cascade(session):
    u = User(username="alice", password_hash="x")
    session.add(u)
    session.flush()
    s = Script(owner_id=u.id, topic="t", editor_text="x")
    session.add(s)
    session.flush()
    sy = Synthesis(owner_id=u.id, script_id=s.id, engine="voxcpm", voice_map={}, speed_map={})
    session.add(sy)
    session.commit()
    syid = sy.id

    session.delete(u)
    session.commit()

    assert session.get(Synthesis, syid) is None


def test_script_share_token_unique(session):
    u = User(username="alice", password_hash="x")
    s1 = Script(owner=u, topic="a", share_token="tok-1")
    s2 = Script(owner=u, topic="b", share_token="tok-1")  # duplicate
    session.add_all([u, s1, s2])
    import sqlalchemy
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        session.commit()
    session.rollback()


def test_soft_delete_via_deleted_at(session):
    u = User(username="alice", password_hash="x")
    session.add(u)
    session.flush()
    a = Article(owner_id=u.id, title="t", source_type="paste", body="b")
    session.add(a)
    session.commit()

    # soft delete
    a.deleted_at = datetime.now(timezone.utc)
    session.commit()

    # Row still in DB
    assert session.get(Article, a.id) is not None
    # But has deleted_at set
    a_fresh = session.get(Article, a.id)
    assert a_fresh.deleted_at is not None
