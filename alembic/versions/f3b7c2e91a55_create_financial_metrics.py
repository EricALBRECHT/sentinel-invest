"""create financial_metrics

Revision ID: f3b7c2e91a55
Revises: a1c4e8b92d10
Create Date: 2026-10-05 17:12:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3b7c2e91a55"
down_revision: Union[str, Sequence[str], None] = "a1c4e8b92d10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "financial_metrics",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("fiscal_period", sa.String(length=2), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("filed_at", sa.Date(), nullable=True),
        sa.Column("revenue", sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column("gross_profit", sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column("operating_income", sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column("net_income", sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column("eps_basic", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("eps_diluted", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("operating_cash_flow", sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column("capital_expenditure", sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column("free_cash_flow", sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column("cash_and_equivalents", sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column("total_assets", sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column("total_liabilities", sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column("total_debt", sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column("shareholders_equity", sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column("shares_outstanding", sa.Numeric(precision=20, scale=4), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_url", sa.String(length=512), nullable=True),
        sa.Column("filing_type", sa.String(length=16), nullable=True),
        sa.Column("accession_number", sa.String(length=32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "fiscal_period IN ('FY', 'Q1', 'Q2', 'Q3', 'Q4')",
            name="ck_financial_metrics_fiscal_period",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "fiscal_year",
            "fiscal_period",
            "source",
            name="uq_financial_metrics_company_period_source",
        ),
    )
    op.create_index(op.f("ix_financial_metrics_id"), "financial_metrics", ["id"], unique=False)
    op.create_index(
        op.f("ix_financial_metrics_company_id"),
        "financial_metrics",
        ["company_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_financial_metrics_company_id"), table_name="financial_metrics")
    op.drop_index(op.f("ix_financial_metrics_id"), table_name="financial_metrics")
    op.drop_table("financial_metrics")
