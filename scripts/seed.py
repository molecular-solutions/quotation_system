"""Load seed/products.csv into an empty products table (safe to re-run: skips if products exist).

    python scripts/seed.py            # only when there are no products yet
    python scripts/seed.py --force    # upsert by Product ID anyway (overwrites edits to those rows)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import migrations
from app.database import SessionLocal
from app.models import Product
from app.services import importer
from app.services.tables import SPECS

SEED = Path(__file__).resolve().parents[1] / "seed" / "products.csv"


def main(force: bool = False) -> None:
    migrations.upgrade()
    with SessionLocal() as db:
        if db.query(Product).count() and not force:
            print("products already exist – nothing seeded (use --force to upsert)")
            return
        prev = importer.preview(db, SPECS["products"], SEED.name, SEED.read_bytes())
        for r in prev.rows:
            if r.errors:
                print("row", r.n, r.errors)
        ok, failed = importer.commit(db, SPECS["products"], prev)
        print(f"seeded {ok} products ({failed} failed)")


if __name__ == "__main__":
    main("--force" in sys.argv)
