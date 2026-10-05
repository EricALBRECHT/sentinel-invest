"""add financial metric source concepts

Revision ID: c4e8a1b73d20
Revises: b8c4d2e71f06
Create Date: 2026-10-05 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4e8a1b73d20"
down_revision: Union[str, Sequence[str], None] = "b8c4d2e71f06"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONCEPT_COLUMNS = (
    "revenue_source_concept",
    "gross_profit_source_concept",
    "operating_income_source_concept",
    "net_income_source_concept",
    "eps_basic_source_concept",
    "eps_diluted_source_concept",
    "operating_cash_flow_source_concept",
    "capital_expenditure_source_concept",
    "cash_source_concept",
    "assets_source_concept",
    "liabilities_source_concept",
    "debt_source_concept",
    "equity_source_concept",
    "shares_source_concept",
)


def upgrade() -> None:
    for name in _CONCEPT_COLUMNS:
        op.add_column(
            "financial_metrics",
            sa.Column(name, sa.String(length=160), nullable=True),
        )


def downgrade() -> None:
    for name in reversed(_CONCEPT_COLUMNS):
        op.drop_column("financial_metrics", name)
