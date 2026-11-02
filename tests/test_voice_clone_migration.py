"""voice_clones 的 alembic 修订可以重复跑，也不碰真实的 podcast.db。"""
import importlib.util
from pathlib import Path

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Column, Integer, String, create_engine, inspect, text
from sqlalchemy.orm import declarative_base

REV_PATH = (
    Path(__file__).resolve().parents[1]
    / "gui/web/migrations/versions/d5f8a21c6e40_add_voice_clones_minimax_columns.py"
)


def _load_revision():
    spec = importlib.util.spec_from_file_location("voice_clone_rev_d5f8a21c6e40", REV_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(engine, fn_name: str) -> None:
    mod = _load_revision()
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            getattr(mod, fn_name)()


def test_upgrade_adds_columns_and_is_idempotent(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    base = declarative_base()

    class VoiceClone(base):
        __tablename__ = "voice_clones"
        id = Column(Integer, primary_key=True)
        voice_id = Column(String(64))

    base.metadata.create_all(engine)
    _run(engine, "upgrade")
    _run(engine, "upgrade")
    names = {c["name"] for c in inspect(engine).get_columns("voice_clones")}
    assert {"minimax_voice_id", "clone_status", "clone_error"} <= names


def test_upgrade_skips_when_table_missing(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    with engine.begin() as conn:
        conn.execute(text("SELECT 1"))
    _run(engine, "upgrade")
    assert "voice_clones" not in inspect(engine).get_table_names()


def test_downgrade_drops_only_existing_columns(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    base = declarative_base()

    class VoiceClone(base):
        __tablename__ = "voice_clones"
        id = Column(Integer, primary_key=True)
        voice_id = Column(String(64))

    base.metadata.create_all(engine)
    _run(engine, "upgrade")
    _run(engine, "downgrade")
    names = {c["name"] for c in inspect(engine).get_columns("voice_clones")}
    assert "minimax_voice_id" not in names
    assert "voice_id" in names
