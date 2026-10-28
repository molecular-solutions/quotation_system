"""quotes.gst_included: print the price with GST included, or '+ GST' (no separate GST rows)

Revision ID: 0004
Revises: 0003
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default 1: every existing quote already showed its total including GST
    with op.batch_alter_table("quotes") as batch:
        batch.add_column(sa.Column("gst_included", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    with op.batch_alter_table("quotes") as batch:
        batch.drop_column("gst_included")
