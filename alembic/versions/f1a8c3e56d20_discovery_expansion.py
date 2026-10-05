"""discovery expansion pipeline

Revision ID: f1a8c3e56d20
Revises: e9c4a7d28b15
Create Date: 2026-10-05 22:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f1a8c3e56d20"
down_revision: Union[str, Sequence[str], None] = "e9c4a7d28b15"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column("discovery_pipeline_status", sa.String(length=16), nullable=False, server_default="NEW"),
    )
    op.add_column("companies", sa.Column("last_discovery_collection_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "companies",
        sa.Column("last_relationship_processing_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("companies", sa.Column("discovery_depth", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("companies", sa.Column("discovered_parent_company_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_companies_discovered_parent_company_id",
        "companies",
        "companies",
        ["discovered_parent_company_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_companies_discovered_parent_company_id",
        "companies",
        ["discovered_parent_company_id"],
    )
    op.create_index("ix_companies_discovery_pipeline_status", "companies", ["discovery_pipeline_status"])
    op.create_check_constraint(
        "ck_companies_discovery_pipeline_status",
        "companies",
        "discovery_pipeline_status IN ('NEW', 'READY', 'COLLECTING', 'ANALYZED', 'BLOCKED')",
    )
    op.create_check_constraint("ck_companies_discovery_depth", "companies", "discovery_depth >= 0")
    op.create_table(
        "company_external_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["external_sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "source_id", name="uq_company_external_sources_company_source"),
    )
    op.create_index("ix_company_external_sources_id", "company_external_sources", ["id"])
    op.create_index("ix_company_external_sources_company_id", "company_external_sources", ["company_id"])
    op.create_index("ix_company_external_sources_source_id", "company_external_sources", ["source_id"])
    op.execute(
        """
        UPDATE companies AS child
        SET discovered_parent_company_id = candidate.discovered_from_company_id,
            discovery_depth = 1,
            discovery_pipeline_status = CASE
                WHEN child.sec_cik IS NOT NULL AND child.sec_cik <> '' AND child.ticker IS NOT NULL THEN 'READY'
                ELSE 'NEW'
            END
        FROM discovered_companies AS candidate
        WHERE candidate.promoted_company_id = child.id
          AND candidate.discovered_from_company_id IS NOT NULL
          AND candidate.discovered_from_company_id <> child.id
        """
    )


def downgrade() -> None:
    op.drop_index("ix_company_external_sources_source_id", table_name="company_external_sources")
    op.drop_index("ix_company_external_sources_company_id", table_name="company_external_sources")
    op.drop_index("ix_company_external_sources_id", table_name="company_external_sources")
    op.drop_table("company_external_sources")
    op.drop_constraint("ck_companies_discovery_depth", "companies", type_="check")
    op.drop_constraint("ck_companies_discovery_pipeline_status", "companies", type_="check")
    op.drop_index("ix_companies_discovery_pipeline_status", table_name="companies")
    op.drop_index("ix_companies_discovered_parent_company_id", table_name="companies")
    op.drop_constraint("fk_companies_discovered_parent_company_id", "companies", type_="foreignkey")
    op.drop_column("companies", "discovered_parent_company_id")
    op.drop_column("companies", "discovery_depth")
    op.drop_column("companies", "last_relationship_processing_at")
    op.drop_column("companies", "last_discovery_collection_at")
    op.drop_column("companies", "discovery_pipeline_status")
