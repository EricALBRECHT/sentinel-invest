"""supply chain discovery

Revision ID: d8e4b2c91a07
Revises: c7f2a8d15e91
Create Date: 2026-10-05 21:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d8e4b2c91a07"
down_revision: Union[str, Sequence[str], None] = "c7f2a8d15e91"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TYPES = (
    "('CUSTOMER', 'SUPPLIER', 'PARTNER', 'COMPETITOR', 'INVESTOR', 'SUBCONTRACTOR', "
    "'EQUIPMENT_PROVIDER', 'MATERIAL_PROVIDER', 'INFRASTRUCTURE_PROVIDER', 'OTHER')"
)
_STATUS = "('DISCOVERED', 'CONFIRMED', 'REJECTED', 'STALE')"
_METHODS = "('RULE', 'DOCUMENT', 'MANUAL', 'SEC', 'OTHER')"
_IMPORTANCE = "('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')"
_CANDIDATE_STATUS = "('CANDIDATE', 'VERIFIED', 'IMPORTED', 'REJECTED')"


def upgrade() -> None:
    op.create_table(
        "company_relationships",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_company_id", sa.Integer(), nullable=False),
        sa.Column("target_company_id", sa.Integer(), nullable=False),
        sa.Column("relationship_type", sa.String(length=32), nullable=False),
        sa.Column("direction", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("importance", sa.String(length=16), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("discovery_method", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("source_company_id != target_company_id", name="ck_company_relationships_not_self"),
        sa.CheckConstraint(f"relationship_type IN {_TYPES}", name="ck_company_relationships_type"),
        sa.CheckConstraint("direction IN ('SOURCE_TO_TARGET')", name="ck_company_relationships_direction"),
        sa.CheckConstraint(f"status IN {_STATUS}", name="ck_company_relationships_status"),
        sa.CheckConstraint(f"discovery_method IN {_METHODS}", name="ck_company_relationships_method"),
        sa.CheckConstraint(f"importance IN {_IMPORTANCE}", name="ck_company_relationships_importance"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 100", name="ck_company_relationships_confidence"),
        sa.CheckConstraint("evidence_count >= 0", name="ck_company_relationships_evidence_count"),
        sa.ForeignKeyConstraint(["source_company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_company_id",
            "target_company_id",
            "relationship_type",
            name="uq_company_relationships_source_target_type",
        ),
    )
    op.create_index("ix_company_relationships_id", "company_relationships", ["id"])
    op.create_index("ix_company_relationships_source_company_id", "company_relationships", ["source_company_id"])
    op.create_index("ix_company_relationships_target_company_id", "company_relationships", ["target_company_id"])
    op.create_table(
        "relationship_evidence",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("relationship_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=True),
        sa.Column("event_id", sa.Integer(), nullable=True),
        sa.Column("evidence_text", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 100", name="ck_relationship_evidence_confidence"),
        sa.ForeignKeyConstraint(["document_id"], ["external_documents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["event_id"], ["intelligence_events.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["relationship_id"], ["company_relationships.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_relationship_evidence_id", "relationship_evidence", ["id"])
    op.create_index("ix_relationship_evidence_relationship_id", "relationship_evidence", ["relationship_id"])
    op.create_index("ix_relationship_evidence_document_id", "relationship_evidence", ["document_id"])
    op.create_index(
        "uq_relationship_evidence_document",
        "relationship_evidence",
        ["relationship_id", "document_id"],
        unique=True,
        postgresql_where=sa.text("document_id IS NOT NULL"),
    )
    op.create_table(
        "discovered_companies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("ticker", sa.String(length=32), nullable=True),
        sa.Column("country", sa.String(length=64), nullable=True),
        sa.Column("website", sa.String(length=255), nullable=True),
        sa.Column("discovered_from_company_id", sa.Integer(), nullable=False),
        sa.Column("discovery_reason", sa.String(length=500), nullable=False),
        sa.Column("evidence_count", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("document_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"status IN {_CANDIDATE_STATUS}", name="ck_discovered_companies_status"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 100", name="ck_discovered_companies_confidence"),
        sa.CheckConstraint("evidence_count >= 0", name="ck_discovered_companies_evidence_count"),
        sa.ForeignKeyConstraint(["discovered_from_company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_discovered_companies_name"),
    )
    op.create_index("ix_discovered_companies_id", "discovered_companies", ["id"])
    op.create_index(
        "ix_discovered_companies_discovered_from_company_id",
        "discovered_companies",
        ["discovered_from_company_id"],
    )


def downgrade() -> None:
    op.drop_table("discovered_companies")
    op.drop_index("uq_relationship_evidence_document", table_name="relationship_evidence")
    op.drop_table("relationship_evidence")
    op.drop_table("company_relationships")
