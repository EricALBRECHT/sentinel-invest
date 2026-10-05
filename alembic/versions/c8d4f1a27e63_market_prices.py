"""market prices and snapshots

Revision ID: c8d4f1a27e63
Revises: a7c3e9b14d62
Create Date: 2026-10-05 19:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8d4f1a27e63"
down_revision: Union[str, Sequence[str], None] = "a7c3e9b14d62"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("market_symbol", sa.String(length=32), nullable=True))
    op.create_index("ix_companies_market_symbol", "companies", ["market_symbol"])
    op.create_table(
        "market_prices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("high", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("low", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("close", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("adjusted_close", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "trade_date", "source", name="uq_market_prices_company_date_source"),
    )
    op.create_index("ix_market_prices_id", "market_prices", ["id"])
    op.create_index("ix_market_prices_company_trade_date", "market_prices", ["company_id", "trade_date"])
    op.create_table(
        "company_market_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("price", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("previous_close", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("market_cap", sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column("average_volume_20d", sa.BigInteger(), nullable=True),
        sa.Column("change_1d_pct", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("change_5d_pct", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("change_1m_pct", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("change_3m_pct", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("change_1y_pct", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("week_52_high", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("week_52_low", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("last_market_date", sa.Date(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id"),
    )
    op.create_index("ix_company_market_snapshots_id", "company_market_snapshots", ["id"])
    op.create_index("ix_company_market_snapshots_company_id", "company_market_snapshots", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_company_market_snapshots_company_id", table_name="company_market_snapshots")
    op.drop_index("ix_company_market_snapshots_id", table_name="company_market_snapshots")
    op.drop_table("company_market_snapshots")
    op.drop_index("ix_market_prices_company_trade_date", table_name="market_prices")
    op.drop_index("ix_market_prices_id", table_name="market_prices")
    op.drop_table("market_prices")
    op.drop_index("ix_companies_market_symbol", table_name="companies")
    op.drop_column("companies", "market_symbol")
