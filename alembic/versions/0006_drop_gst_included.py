"""drop quotes.gst_included: every quote is priced including GST, there is no "+ GST" choice

Revision ID: 0006
Revises: 0005
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("quotes") as batch:
        batch.drop_column("gst_included")


def downgrade() -> None:
    with op.batch_alter_table("quotes") as batch:
        batch.add_column(sa.Column("gst_included", sa.Boolean(), nullable=False, server_default=sa.true()))
