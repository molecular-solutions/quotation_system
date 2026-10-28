"""Run Alembic migrations at startup (and adopt a database created before migrations existed)."""
from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from app import config
from app.database import engine

ROOT = config.BASE_DIR


def alembic_config(url: str | None = None) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    return cfg


def upgrade() -> None:
    cfg = alembic_config()
    tables = set(inspect(engine).get_table_names())
    if "products" in tables and "alembic_version" not in tables:
        command.stamp(cfg, "head")  # schema already there (created by an earlier dev run)
    else:
        command.upgrade(cfg, "head")
