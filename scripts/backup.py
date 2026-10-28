"""Consistent SQLite backup (safe while the app is running) into <data>/backups, keeping the newest 14."""
import datetime as dt
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import config


def main(keep: int = 14) -> None:
    if not config.DATABASE_URL.startswith("sqlite"):
        print("backup.py only handles SQLite; use pg_dump for Postgres")
        return
    src_path = Path(config.DATABASE_URL.removeprefix("sqlite:///"))
    if not src_path.exists():
        print("no database yet")
        return
    out_dir = config.DATA_DIR / "backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"quotes-{dt.date.today():%Y%m%d}.db"
    src, dst = sqlite3.connect(src_path), sqlite3.connect(dest)
    with dst:
        src.backup(dst)
    src.close(); dst.close()
    for old in sorted(out_dir.glob("quotes-*.db"))[:-keep]:
        old.unlink()
    print("backup written:", dest)


if __name__ == "__main__":
    main()
