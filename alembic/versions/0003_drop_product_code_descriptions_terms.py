"""drop products.code (name is the key), line descriptions, frozen terms

Revision ID: 0003
Revises: 0002
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    dupes = op.get_bind().execute(sa.text(
        "SELECT lower(name) FROM products GROUP BY lower(name) HAVING count(*) > 1")).fetchall()
    if dupes:
        raise RuntimeError("Two products share a name (" + ", ".join(d[0] for d in dupes) + "). "
                           "Rename one of them before upgrading: product names are now the unique key.")
    with op.batch_alter_table("products", recreate="always") as batch:
        batch.drop_column("code")
        batch.create_unique_constraint("uq_products_name", ["name"])
    with op.batch_alter_table("quote_lines") as batch:
        batch.add_column(sa.Column("description", sa.Text(), nullable=False, server_default=""))
    with op.batch_alter_table("quotes") as batch:
        batch.add_column(sa.Column("terms", sa.Text(), nullable=True))


def downgrade() -> None:
    raise NotImplementedError("dropping Product ID cannot be undone")
