"""add opportunity coverage fields

Revision ID: e1b6c8d24a30
Revises: d7f2b9c41e18
Create Date: 2026-10-05 18:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e1b6c8d24a30"
down_revision: Union[str, Sequence[str], None] = "d7f2b9c41e18"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "company_opportunity_scores",
        sa.Column("coverage_score", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "company_opportunity_scores",
        sa.Column("coverage_status", sa.String(length=16), nullable=False, server_default="INCOMPLETE"),
    )
    op.add_column(
        "company_opportunity_scores",
        sa.Column("ranking_eligible", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("company_opportunity_scores", "coverage_score", server_default=None)
    op.alter_column("company_opportunity_scores", "coverage_status", server_default=None)
    op.alter_column("company_opportunity_scores", "ranking_eligible", server_default=None)


def downgrade() -> None:
    op.drop_column("company_opportunity_scores", "ranking_eligible")
    op.drop_column("company_opportunity_scores", "coverage_status")
    op.drop_column("company_opportunity_scores", "coverage_score")
