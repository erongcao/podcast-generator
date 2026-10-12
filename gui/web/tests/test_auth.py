"""
test_auth.py — Auth layer tests (register/login/logout/me, deps)

Uses an isolated in-memory SQLite DB per test to avoid touching
the user's actual DB at ~/.podcast-automation/podcast.db.

Fixtures:
    - engine: in-memory SQLite
    - db: fresh session per test
    - client: TestClient with overridden get_db
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

from gui.web.db import Base, get_db
from gui.web.app import app
from gui.web.models import User, AuditLog


@pytest.fixture
def engine():
    """In-memory SQLite engine with shared connection (StaticPool for thread safety)."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    """Per-test session."""
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def client(engine, monkeypatch):
    """TestClient with get_db overridden + audit() SessionLocal patched.

    The audit() helper in auth.py opens its OWN SessionLocal() rather than
    accepting a session — so we have to swap gui.web.db.SessionLocal too,
    otherwise audit entries go to the user's real on-disk DB and the
    test's in-memory queries miss them.
    """
    SessionLocal = sessionmaker(bind=engine)

    def override_get_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr("gui.web.db.SessionLocal", SessionLocal)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# === register ===

def test_register_first_user_becomes_admin(client, db):
    """First user is admin (bootstrap)."""
    res = client.post("/api/auth/register", json={
        "username": "first",
        "password": "firstpass1",
    })
    assert res.status_code == 200
    data = res.json()
    assert data["username"] == "first"
    assert data["is_admin"] is True
    # Session cookie should be set (auto-login)
    assert "session" in res.cookies


def test_register_second_user_not_admin(client):
    """Second user is not admin."""
    client.post("/api/auth/register", json={"username": "first", "password": "firstpass1"})
    res = client.post("/api/auth/register", json={"username": "second", "password": "secondpass2"})
    assert res.status_code == 200
    assert res.json()["is_admin"] is False


def test_register_duplicate_username_409(client):
    """Duplicate username returns 409."""
    client.post("/api/auth/register", json={"username": "alice", "password": "pass1234"})
    res = client.post("/api/auth/register", json={"username": "alice", "password": "pass1234"})
    assert res.status_code == 409


def test_register_validates_password_length(client):
    """Short password rejected by Pydantic."""
    res = client.post("/api/auth/register", json={"username": "alice", "password": "short"})
    assert res.status_code == 422


def test_register_validates_username_length(client):
    """Short username rejected."""
    res = client.post("/api/auth/register", json={"username": "ab", "password": "abcdefgh"})
    assert res.status_code == 422


# === login ===

def test_login_success(client):
    client.post("/api/auth/register", json={"username": "alice", "password": "pass1234"})
    res = client.post("/api/auth/login", json={"username": "alice", "password": "pass1234"})
    assert res.status_code == 200
    assert "session" in res.cookies


def test_login_wrong_password_401(client):
    client.post("/api/auth/register", json={"username": "alice", "password": "pass1234"})
    res = client.post("/api/auth/login", json={"username": "alice", "password": "wrongpass"})
    assert res.status_code == 401
    # Generic error message (no enumeration)
    assert res.json()["detail"] == "用户名或密码错误"


def test_login_nonexistent_user_401(client):
    res = client.post("/api/auth/login", json={"username": "nobody", "password": "any12345"})
    assert res.status_code == 401


# === me ===

def test_me_unauthenticated_401(client):
    res = client.get("/api/auth/me")
    assert res.status_code == 401
    assert res.json()["detail"] == "Not authenticated"


def test_me_authenticated(client):
    client.post("/api/auth/register", json={
        "username": "doctor",
        "password": "doctorpass",
        "display_name": "Dr Smith",
    })
    res = client.get("/api/auth/me")
    assert res.status_code == 200
    data = res.json()
    assert data["username"] == "doctor"
    assert data["display_name"] == "Dr Smith"
    assert data["is_admin"] is True


def test_me_after_logout_401(client):
    client.post("/api/auth/register", json={"username": "alice", "password": "pass1234"})
    client.post("/api/auth/logout")
    res = client.get("/api/auth/me")
    assert res.status_code == 401


# === logout ===

def test_logout_clears_session(client):
    client.post("/api/auth/register", json={"username": "alice", "password": "pass1234"})
    res = client.post("/api/auth/logout")
    assert res.status_code == 200
    # Next /me should fail
    assert client.get("/api/auth/me").status_code == 401


def test_logout_when_not_logged_in_still_200(client):
    """Logout is idempotent — no session is fine."""
    res = client.post("/api/auth/logout")
    assert res.status_code == 200


# === audit log ===

def test_login_failed_creates_audit_entry(client, db):
    """Failed login writes audit log."""
    client.post("/api/auth/register", json={"username": "alice", "password": "pass1234"})
    client.post("/api/auth/login", json={"username": "alice", "password": "wrong"})
    entries = db.query(AuditLog).filter(AuditLog.event_type == "user.login.failed").all()
    assert len(entries) == 1
    assert entries[0].user_id is None  # failed logins have no user_id


def test_successful_login_creates_audit_entry(client, db):
    client.post("/api/auth/register", json={"username": "alice", "password": "pass1234"})
    client.post("/api/auth/logout")  # logout so /login below is a fresh login
    client.post("/api/auth/login", json={"username": "alice", "password": "pass1234"})
    entries = db.query(AuditLog).filter(AuditLog.event_type == "user.login").all()
    assert len(entries) >= 1
    assert entries[0].user_id is not None
