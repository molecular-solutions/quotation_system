"""Copy an existing SQLite database into the (empty) database named by DATABASE_URL, normally Postgres.

    DATABASE_URL=postgresql://user:pass@host/db python scripts/sqlite_to_postgres.py /data/quotes.db

Creates the schema if the target is new, refuses to run when any target table already has rows, copies every table in
foreign-key order inside one transaction, resets the Postgres id sequences and checks the row counts match. The SQLite file is
opened read-only and is not changed. Stop the app first so nothing is written during the copy."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine, func, select, text

from app import config, migrations
from app.database import Base, engine as target


def main(source_path: str) -> None:
    if target.dialect.name == "sqlite":
        sys.exit("DATABASE_URL still points at SQLite; set it to the Postgres database to copy into")
    src_file = Path(source_path)
    if not src_file.exists():
        sys.exit(f"no such file: {src_file}")
    source = create_engine(f"sqlite:///file:{src_file}?mode=ro&uri=true")

    with source.connect() as c:
        src_version = c.execute(text("SELECT version_num FROM alembic_version")).scalar()
    migrations.upgrade()  # new database: schema straight from the models, stamped at head
    with target.connect() as c:
        head = c.execute(text("SELECT version_num FROM alembic_version")).scalar()
    if src_version != head:
        sys.exit(f"the SQLite file is at migration {src_version} but the app is at {head}; start the app on it once to migrate it first")

    tables = Base.metadata.sorted_tables  # parents before children
    with target.connect() as c:
        filled = [t.name for t in tables if c.execute(select(func.count()).select_from(t)).scalar()]
    if filled:
        sys.exit(f"the target is not empty (rows in: {', '.join(filled)}); refusing to copy over it")

    counts = {}
    with source.connect() as s, target.begin() as d:
        for t in tables:
            rows = [dict(r._mapping) for r in s.execute(select(t))]
            if rows:
                d.execute(t.insert(), rows)
            counts[t.name] = len(rows)
        for t in tables:  # keep new ids after the copied ones
            pk = [c for c in t.primary_key.columns if c.autoincrement is not False and c.type.python_type is int]
            if len(pk) == 1:
                d.execute(text("SELECT setval(pg_get_serial_sequence(:t, :c), COALESCE((SELECT MAX({c}) FROM {t}), 1), "
                               "(SELECT MAX({c}) FROM {t}) IS NOT NULL)".format(t=t.name, c=pk[0].name)),
                          {"t": t.name, "c": pk[0].name})

    with target.connect() as c:
        got = {t.name: c.execute(select(func.count()).select_from(t)).scalar() for t in tables}
    bad = {k: (counts[k], got[k]) for k in counts if counts[k] != got[k]}
    if bad:
        sys.exit(f"row counts differ (source, target): {bad}")
    for name, n in counts.items():
        print(f"{name:28} {n:>6}")
    print(f"copied {sum(counts.values())} rows into {target.url.render_as_string(hide_password=True)}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
