"""quotes.emailed_at: when the quotation was last e-mailed from the app

Revision ID: 0005
Revises: 0004
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("quotes") as batch:
        batch.add_column(sa.Column("emailed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("quotes") as batch:
        batch.drop_column("emailed_at")
