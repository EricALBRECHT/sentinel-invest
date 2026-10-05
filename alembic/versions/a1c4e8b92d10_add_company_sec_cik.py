"""add company sec_cik

Revision ID: a1c4e8b92d10
Revises: b7e2a4c91d08
Create Date: 2026-10-05 17:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1c4e8b92d10"
down_revision: Union[str, Sequence[str], None] = "b7e2a4c91d08"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("sec_cik", sa.String(length=10), nullable=True))
    op.create_index(op.f("ix_companies_sec_cik"), "companies", ["sec_cik"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_companies_sec_cik"), table_name="companies")
    op.drop_column("companies", "sec_cik")
