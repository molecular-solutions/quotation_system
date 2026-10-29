"""product_prices.three_year_price: the price of a 3-year quote, which is not simply 3 x the annual price

Revision ID: 0008
Revises: 0007
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing rows keep their annual price untouched; NULL = no 3-year price yet.
    op.add_column("product_prices", sa.Column("three_year_price", sa.Numeric(14, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("product_prices", "three_year_price")
