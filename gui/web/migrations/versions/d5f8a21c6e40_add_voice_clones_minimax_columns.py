"""add voice_clones minimax clone columns

Revision ID: d5f8a21c6e40
Revises: 86e2ab3c95ab
Create Date: 2026-11-02

启动器不跑 alembic，gui.web.app 里的 _ensure_voice_clone_columns() 仍会
给老库 ALTER。两条路都做成幂等：列已经在（create_all 或启动 ALTER 加过）
就跳过。表还不存在也跳过，交给 create_all。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d5f8a21c6e40"
down_revision: Union[str, Sequence[str], None] = "86e2ab3c95ab"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = (
    ("minimax_voice_id", sa.String(length=128)),
    ("clone_status", sa.String(length=16)),
    ("clone_error", sa.Text()),
)


def _existing_columns() -> set[str] | None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "voice_clones" not in inspector.get_table_names():
        return None
    return {c["name"] for c in inspector.get_columns("voice_clones")}


def upgrade() -> None:
    existing = _existing_columns()
    if existing is None:
        return
    for name, col_type in _COLUMNS:
        if name not in existing:
            op.add_column("voice_clones", sa.Column(name, col_type, nullable=True))


def downgrade() -> None:
    existing = _existing_columns()
    if existing is None:
        return
    for name, _col_type in reversed(_COLUMNS):
        if name in existing:
            op.drop_column("voice_clones", name)
