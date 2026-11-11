"""Run Alembic migrations at startup (and adopt a database created before migrations existed)."""
from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

import app.models  # noqa: F401  (registers the tables on Base.metadata)
from app import config
from app.database import Base, engine

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
    elif not tables and engine.dialect.name != "sqlite":
        # A new Postgres database: the older migrations were written for SQLite (they rebuild tables), and the models
        # already are the head schema (tests/test_migrations.py checks that), so build it directly and mark it current.
        Base.metadata.create_all(engine)
        command.stamp(cfg, "head")
    else:
        command.upgrade(cfg, "head")
