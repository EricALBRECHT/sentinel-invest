"""market cap provenance and provider symbols

Revision ID: d1e7a4c92b18
Revises: c8d4f1a27e63
Create Date: 2026-10-05 19:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d1e7a4c92b18"
down_revision: Union[str, Sequence[str], None] = "c8d4f1a27e63"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("company_market_snapshots", sa.Column("market_cap_source", sa.String(length=32), nullable=True))
    op.add_column("company_market_snapshots", sa.Column("market_cap_method", sa.String(length=32), nullable=True))
    op.add_column("company_market_snapshots", sa.Column("market_cap_as_of", sa.Date(), nullable=True))
    op.add_column("company_market_snapshots", sa.Column("market_cap_confidence", sa.String(length=8), nullable=True))
    op.add_column("company_market_snapshots", sa.Column("market_cap_reason", sa.String(length=500), nullable=True))
    op.create_check_constraint(
        "ck_company_market_snapshots_cap_method",
        "company_market_snapshots",
        "market_cap_method IS NULL OR market_cap_method IN ('PROVIDER', 'PRICE_X_SHARES')",
    )
    op.create_check_constraint(
        "ck_company_market_snapshots_cap_confidence",
        "company_market_snapshots",
        "market_cap_confidence IS NULL OR market_cap_confidence IN ('HIGH', 'MEDIUM', 'LOW')",
    )
    op.create_table(
        "market_provider_symbols",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_symbol", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "provider", name="uq_market_provider_symbols_company_provider"),
    )
    op.create_index("ix_market_provider_symbols_id", "market_provider_symbols", ["id"])
    op.create_index("ix_market_provider_symbols_company_id", "market_provider_symbols", ["company_id"])
    op.execute(
        """
        INSERT INTO market_provider_symbols (company_id, provider, provider_symbol)
        SELECT id, 'yahoo', 'STMPA.PA'
        FROM companies
        WHERE market_symbol = 'STM.PA'
        """
    )


def downgrade() -> None:
    op.drop_index("ix_market_provider_symbols_company_id", table_name="market_provider_symbols")
    op.drop_index("ix_market_provider_symbols_id", table_name="market_provider_symbols")
    op.drop_table("market_provider_symbols")
    op.drop_constraint("ck_company_market_snapshots_cap_confidence", "company_market_snapshots", type_="check")
    op.drop_constraint("ck_company_market_snapshots_cap_method", "company_market_snapshots", type_="check")
    op.drop_column("company_market_snapshots", "market_cap_reason")
    op.drop_column("company_market_snapshots", "market_cap_confidence")
    op.drop_column("company_market_snapshots", "market_cap_as_of")
    op.drop_column("company_market_snapshots", "market_cap_method")
    op.drop_column("company_market_snapshots", "market_cap_source")
