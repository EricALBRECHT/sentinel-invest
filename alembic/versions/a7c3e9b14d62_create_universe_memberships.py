"""create universe memberships

Revision ID: a7c3e9b14d62
Revises: f2a9d4c71b55
Create Date: 2026-10-05 19:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a7c3e9b14d62"
down_revision: Union[str, Sequence[str], None] = "f2a9d4c71b55"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column("universe_status", sa.String(length=32), server_default="DISCOVERED", nullable=False),
    )
    op.add_column(
        "companies",
        sa.Column("universe_priority", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("companies", sa.Column("discovery_source", sa.String(length=32), nullable=True))
    op.add_column("companies", sa.Column("discovery_reason", sa.String(length=500), nullable=True))
    op.add_column(
        "companies",
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.add_column("companies", sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("companies", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE companies SET first_seen_at = created_at, last_seen_at = created_at")
    op.create_check_constraint(
        "ck_companies_universe_status",
        "companies",
        "universe_status IN ('DISCOVERED', 'WATCHED', 'SCREENED', 'DEEP_ANALYSIS', 'PORTFOLIO', 'ARCHIVED')",
    )
    op.create_check_constraint(
        "ck_companies_universe_priority",
        "companies",
        "universe_priority >= 0 AND universe_priority <= 100",
    )
    op.create_check_constraint(
        "ck_companies_discovery_source",
        "companies",
        "discovery_source IS NULL OR discovery_source IN "
        "('MANUAL', 'SP500', 'NASDAQ100', 'PEA', 'EUROPE', 'SUPPLY_CHAIN', 'BOTTLENECK', 'INSTITUTIONAL', 'OTHER')",
    )
    op.create_table(
        "universe_memberships",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("universe_name", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_universe_memberships_id", "universe_memberships", ["id"])
    op.create_index("ix_universe_memberships_company_id", "universe_memberships", ["company_id"])
    op.create_index("ix_universe_memberships_universe_name", "universe_memberships", ["universe_name"])
    op.create_index(
        "uq_universe_membership_active",
        "universe_memberships",
        ["company_id", "universe_name", "source"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )


def downgrade() -> None:
    op.drop_index("uq_universe_membership_active", table_name="universe_memberships")
    op.drop_index("ix_universe_memberships_universe_name", table_name="universe_memberships")
    op.drop_index("ix_universe_memberships_company_id", table_name="universe_memberships")
    op.drop_index("ix_universe_memberships_id", table_name="universe_memberships")
    op.drop_table("universe_memberships")
    op.drop_constraint("ck_companies_discovery_source", "companies", type_="check")
    op.drop_constraint("ck_companies_universe_priority", "companies", type_="check")
    op.drop_constraint("ck_companies_universe_status", "companies", type_="check")
    op.drop_column("companies", "last_seen_at")
    op.drop_column("companies", "first_seen_at")
    op.drop_column("companies", "is_active")
    op.drop_column("companies", "discovery_reason")
    op.drop_column("companies", "discovery_source")
    op.drop_column("companies", "universe_priority")
    op.drop_column("companies", "universe_status")
