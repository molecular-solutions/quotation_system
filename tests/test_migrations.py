from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

import app.models  # noqa: F401
from app import migrations
from app.database import Base


def test_migrations_build_exactly_the_model_schema(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    from app import config
    monkeypatch.setattr(config, "DATABASE_URL", url)
    command.upgrade(migrations.alembic_config(), "head")
    engine = create_engine(url)
    assert set(inspect(engine).get_table_names()) - {"alembic_version"} == set(Base.metadata.tables)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], f"models changed without a migration: {diff}"
