"""
api/auth.py — register / login / logout / me endpoints
"""
from __future__ import annotations

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from gui.web.db import get_db
from gui.web.models import User
from gui.web.auth import (
    get_current_user, get_current_user_optional,
    hash_password, verify_password, audit,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    email: str | None = None
    display_name: str | None = None


class LoginRequest(BaseModel):
    username: str
    password: str


class UserPublic(BaseModel):
    id: int
    username: str
    email: str | None
    display_name: str | None
    is_admin: bool
    created_at: datetime
    last_login_at: datetime | None

    class Config:
        from_attributes = True


def _to_public(user: User) -> UserPublic:
    return UserPublic(
        id=user.id, username=user.username, email=user.email,
        display_name=user.display_name, is_admin=user.is_admin,
        created_at=user.created_at, last_login_at=user.last_login_at,
    )


@router.post("/register", response_model=UserPublic)
async def register(req: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    """Create a new user account. First-ever user becomes admin (bootstrap).

    Auto-logs-in: sets request.session['user_id'] so the frontend can redirect
    to / without a separate /login call.
    """
    existing = db.query(User).filter(User.username == req.username).first()
    if existing:
        raise HTTPException(409, "用户名已存在")

    # First user becomes admin
    is_first = db.query(User).count() == 0
    user = User(
        username=req.username,
        password_hash=hash_password(req.password),
        email=req.email,
        display_name=req.display_name or req.username,
        is_admin=is_first,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Auto-login: set user_id in session so client is logged in immediately
    request.session["user_id"] = user.id
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()

    audit("user.register", user_id=user.id, request=request,
          metadata={"is_first": is_first})
    return _to_public(user)


@router.post("/login", response_model=UserPublic)
async def login(req: LoginRequest, request: Request, db: Session = Depends(get_db)):
    """Authenticate + start session."""
    user = db.query(User).filter(User.username == req.username).first()
    if not user or not verify_password(req.password, user.password_hash):
        audit("user.login.failed", request=request, metadata={"username": req.username})
        raise HTTPException(401, "用户名或密码错误")

    if not user.is_active:
        raise HTTPException(403, "账号已停用")

    request.session["user_id"] = user.id
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()

    audit("user.login", user_id=user.id, request=request)
    return _to_public(user)


@router.post("/logout")
async def logout(request: Request, user: User | None = Depends(get_current_user_optional)):
    """End session."""
    request.session.clear()
    if user:
        audit("user.logout", user_id=user.id, request=request)
    return {"ok": True}


@router.get("/me", response_model=UserPublic)
async def me(user: User = Depends(get_current_user)):
    """Returns the current user. Used by frontend to check login state on every page load."""
    return _to_public(user)