"""
models.py — ORM models (v0.4.0-web)

Tables:
    users           — accounts (bcrypt password hash)
    articles        — source material (URLs / uploads) → can become multiple scripts
    scripts         — script content (with parse → segments cached)
    syntheses       — async synthesis jobs (engine / status / voice_map / started_at)
    outputs         — produced audio files (mp3 path / duration / size)
    voice_clones    — per-user cloned voice metadata (replaces voices.json)
    api_keys        — per-user TTS credentials (minimax / volcengine / doubao)
    audit_log       — append-only event log for security + debugging

Row-level isolation: every domain table has owner_id → users.id (FK).
"""
from __future__ import annotations

import datetime as dt
from typing import Optional, List
from sqlalchemy import String, Integer, ForeignKey, Text, Boolean, DateTime, Float, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from gui.web.db import Base


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[Optional[str]] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))

    scripts: Mapped[List["Script"]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    voice_clones: Mapped[List["VoiceClone"]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    api_keys: Mapped[List["ApiKey"]] = relationship(back_populates="owner", cascade="all, delete-orphan")


class Article(Base):
    """Source material — original docs / URLs / pasted text."""
    __tablename__ = "articles"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    source_type: Mapped[str] = mapped_column(String(32))  # 'url' | 'file' | 'paste'
    source_url: Mapped[Optional[str]] = mapped_column(String(1024))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))  # soft delete

    scripts: Mapped[List["Script"]] = relationship(back_populates="article")


class Script(Base):
    """A script generated from an article (or manual)."""
    __tablename__ = "scripts"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    article_id: Mapped[Optional[int]] = mapped_column(ForeignKey("articles.id", ondelete="SET NULL"))
    topic: Mapped[str] = mapped_column(String(255), default="未命名脚本")
    editor_text: Mapped[str] = mapped_column(Text, default="")
    segments_json: Mapped[Optional[str]] = mapped_column(Text)  # cached parse result (JSON)
    n_segments: Mapped[int] = mapped_column(Integer, default=0)
    visibility: Mapped[str] = mapped_column(String(16), default="private")  # private | unlisted | public
    share_token: Mapped[Optional[str]] = mapped_column(String(64), unique=True, index=True)
    # original_filename: 兼容 PySide6 时代 import 进来的脚本（原 filename stem），
    # 让 dedup 基于"文件名唯一"而非"(topic, created_at) 可能撞车"的 tuple。
    # 仅 legacy import 路径会填，新建的脚本为 None。
    original_filename: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))

    owner: Mapped["User"] = relationship(back_populates="scripts")
    article: Mapped[Optional["Article"]] = relationship(back_populates="scripts")
    syntheses: Mapped[List["Synthesis"]] = relationship(back_populates="script", cascade="all, delete-orphan")


class Synthesis(Base):
    """Async synthesis job — engine / status / voice_map / cost tracking."""
    __tablename__ = "syntheses"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    script_id: Mapped[int] = mapped_column(ForeignKey("scripts.id", ondelete="CASCADE"), index=True)
    engine: Mapped[str] = mapped_column(String(32))  # 'voxcpm' | 'minimax' | 'doubao'
    voice_map: Mapped[dict] = mapped_column(JSON, default=dict)  # {speaker: voice_id}
    speed_map: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | running | done | failed
    started_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    cost_estimate: Mapped[Optional[float]] = mapped_column(Float)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    script: Mapped["Script"] = relationship(back_populates="syntheses")
    outputs: Mapped[List["Output"]] = relationship(back_populates="synthesis", cascade="all, delete-orphan")


class Output(Base):
    """Produced audio file."""
    __tablename__ = "outputs"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    synthesis_id: Mapped[int] = mapped_column(ForeignKey("syntheses.id", ondelete="CASCADE"), index=True)
    file_path: Mapped[str] = mapped_column(String(1024))
    duration_sec: Mapped[Optional[float]] = mapped_column(Float)
    file_size: Mapped[Optional[int]] = mapped_column(Integer)
    format: Mapped[str] = mapped_column(String(16), default="mp3")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    synthesis: Mapped["Synthesis"] = relationship(back_populates="outputs")


class VoiceClone(Base):
    """Per-user cloned voice metadata (replaces voices.json)."""
    __tablename__ = "voice_clones"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    voice_id: Mapped[str] = mapped_column(String(128), index=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(255))
    ref_text: Mapped[Optional[str]] = mapped_column(Text)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON, default=list)  # ['female', 'foreign', 'voice_design']
    audio_path: Mapped[Optional[str]] = mapped_column(String(1024))
    sample_rate: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    owner: Mapped["User"] = relationship(back_populates="voice_clones")


class ApiKey(Base):
    """Per-user TTS credentials. Stored encrypted (TODO: use Fernet for at-rest)."""
    __tablename__ = "api_keys"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(32))  # 'minimax' | 'volcengine' | 'doubao'
    key_name: Mapped[str] = mapped_column(String(64))  # 'api_key' | 'app_id' | 'access_token' | ...
    encrypted_value: Mapped[str] = mapped_column(Text)  # Fernet-encrypted; placeholder: plaintext + WARNING
    last_used_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    owner: Mapped["User"] = relationship(back_populates="api_keys")


class AuditLog(Base):
    """Append-only event log: who did what when. SECURITY + debugging."""
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    resource_type: Mapped[Optional[str]] = mapped_column(String(64))
    resource_id: Mapped[Optional[int]] = mapped_column(Integer)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64))
    user_agent: Mapped[Optional[str]] = mapped_column(String(512))
    metadata_json: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)