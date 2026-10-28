"""prices per number of users; one SeeSAR and one InfiniSee; quote unit price = annual price for the users

Revision ID: 0007
Revises: 0006
"""
from decimal import ROUND_HALF_UP, Decimal
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (name to remove, name to keep): the old, vendor-labelled rows are folded into the plain ones.
MERGES = [("SeeSAR with YASARA Plugin", "SeeSAR"), ("infiniSee - Three Search Modes", "InfiniSee")]


def upgrade() -> None:
    bind = op.get_bind()

    # 1. product_prices: one row per product x tier x number of users (existing rows become the 1-user price).
    op.create_table(
        "product_prices_new",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tier", sa.String(20), nullable=False),
        sa.Column("users", sa.Integer(), nullable=False),
        sa.Column("annual_price", sa.Numeric(14, 2), nullable=False),
        sa.UniqueConstraint("product_id", "tier", "users", name="uq_product_prices_product_tier_users"),
    )
    op.execute("INSERT INTO product_prices_new (id, product_id, tier, users, annual_price) "
               "SELECT id, product_id, tier, 1, annual_price FROM product_prices")
    op.drop_table("product_prices")
    op.rename_table("product_prices_new", "product_prices")

    # 2. A quote line's unit price used to be per user; it is now the annual price for the line's users, so
    #    amount = unit price x years. Re-express existing lines so every total stays exactly as issued.
    lines = sa.table("quote_lines", sa.column("id", sa.Integer), sa.column("unit_price", sa.Numeric(14, 2)),
                     sa.column("amount", sa.Numeric(14, 2)), sa.column("years", sa.Integer),
                     sa.column("complimentary", sa.Boolean))
    for row in bind.execute(sa.select(lines.c.id, lines.c.amount, lines.c.years).where(lines.c.complimentary == sa.false())):
        if row.years:
            unit = (Decimal(str(row.amount)) / row.years).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            bind.execute(lines.update().where(lines.c.id == row.id).values(unit_price=unit))

    # 3. One SeeSAR and one InfiniSee.
    for drop_name, keep_name in MERGES:
        drop = bind.execute(sa.text("SELECT id FROM products WHERE lower(name) = lower(:n)"), {"n": drop_name}).scalar()
        keep = bind.execute(sa.text("SELECT id FROM products WHERE lower(name) = lower(:n)"), {"n": keep_name}).scalar()
        if drop is None or keep is None or drop == keep:
            continue
        for col in ("vendor", "deployment"):  # fill what the kept row lacks from the one being removed
            bind.execute(sa.text(f"UPDATE products SET {col} = (SELECT {col} FROM products WHERE id = :d) "
                                 f"WHERE id = :k AND ({col} IS NULL OR {col} = '')"), {"d": drop, "k": keep})
        bind.execute(sa.text(
            "INSERT INTO product_catalog_links (product_id, block_key) "
            "SELECT :k, block_key FROM product_catalog_links WHERE product_id = :d "
            "AND block_key NOT IN (SELECT block_key FROM product_catalog_links WHERE product_id = :k)"), {"d": drop, "k": keep})
        bind.execute(sa.text("UPDATE quote_lines SET product_id = :k WHERE product_id = :d"), {"d": drop, "k": keep})
        bind.execute(sa.text("UPDATE package_items SET product_id = :k WHERE product_id = :d"), {"d": drop, "k": keep})
        bind.execute(sa.text("DELETE FROM product_catalog_links WHERE product_id = :d"), {"d": drop})
        bind.execute(sa.text("DELETE FROM product_prices WHERE product_id = :d"), {"d": drop})
        bind.execute(sa.text("DELETE FROM products WHERE id = :d"), {"d": drop})


def downgrade() -> None:
    raise NotImplementedError("per-user-count prices and the merged products cannot be undone")
