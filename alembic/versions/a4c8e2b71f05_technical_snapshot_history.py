"""technical snapshot history

Revision ID: a4c8e2b71f05
Revises: f3a9c6e24d81
Create Date: 2026-10-05 20:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a4c8e2b71f05"
down_revision: Union[str, Sequence[str], None] = "f3a9c6e24d81"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "technical_snapshots",
        sa.Column("method", sa.String(length=32), server_default="technical_v1", nullable=False),
    )
    op.drop_constraint("technical_snapshots_company_id_key", "technical_snapshots", type_="unique")
    op.create_unique_constraint(
        "uq_technical_snapshots_company_date_method",
        "technical_snapshots",
        ["company_id", "as_of_date", "method"],
    )
    op.create_index("ix_technical_snapshots_as_of_date", "technical_snapshots", ["as_of_date"])


def downgrade() -> None:
    op.drop_index("ix_technical_snapshots_as_of_date", table_name="technical_snapshots")
    op.drop_constraint("uq_technical_snapshots_company_date_method", "technical_snapshots", type_="unique")
    op.create_unique_constraint("technical_snapshots_company_id_key", "technical_snapshots", ["company_id"])
    op.drop_column("technical_snapshots", "method")
