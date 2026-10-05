"""create company_scores

Revision ID: b8c4d2e71f06
Revises: f3b7c2e91a55
Create Date: 2026-10-05 17:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b8c4d2e71f06"
down_revision: Union[str, Sequence[str], None] = "f3b7c2e91a55"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "company_scores",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("score_date", sa.Date(), nullable=False),
        sa.Column("quality_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("overall_confidence_score", sa.Integer(), nullable=False),
        sa.Column("revenue_growth_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("profit_growth_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("fcf_growth_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("margins_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("profitability_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("debt_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("cash_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("dilution_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("stability_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("metrics_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("confidence_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("method_version", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "score_date",
            "method_version",
            name="uq_company_scores_company_date_version",
        ),
    )
    op.create_index(op.f("ix_company_scores_id"), "company_scores", ["id"], unique=False)
    op.create_index(op.f("ix_company_scores_company_id"), "company_scores", ["company_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_company_scores_company_id"), table_name="company_scores")
    op.drop_index(op.f("ix_company_scores_id"), table_name="company_scores")
    op.drop_table("company_scores")
