"""external intelligence documents

Revision ID: c7f2a8d15e91
Revises: b6e1d9a42c70
Create Date: 2026-10-05 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c7f2a8d15e91"
down_revision: Union[str, Sequence[str], None] = "b6e1d9a42c70"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SOURCE_TYPES = (
    "('NEWS', 'COMPANY_IR', 'REGULATOR', 'SEC', 'RSS', 'MARKET', 'INDUSTRY', 'GOVERNMENT', 'OTHER')"
)
_DOCUMENT_TYPES = (
    "('NEWS_ARTICLE', 'PRESS_RELEASE', 'SEC_FILING', 'BLOG_POST', "
    "'GOVERNMENT_RELEASE', 'INDUSTRY_ARTICLE', 'OTHER')"
)
_RELATIONS = "('SUBJECT', 'MENTION', 'CUSTOMER', 'SUPPLIER', 'PARTNER', 'COMPETITOR', 'INVESTOR', 'OTHER')"
_EVENTS = (
    "('CONTRACT', 'PARTNERSHIP', 'ACQUISITION', 'INVESTMENT', 'NEW_FACTORY', "
    "'CAPACITY_EXPANSION', 'PRODUCT_LAUNCH', 'CUSTOMER_WIN', 'SUPPLIER_CHANGE', "
    "'REGULATORY', 'MANAGEMENT', 'FINANCING', 'LAYOFF', 'CYBERSECURITY', 'LEGAL', 'OTHER')"
)


def upgrade() -> None:
    op.create_table(
        "external_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("base_url", sa.String(length=1000), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("trust_level", sa.String(length=8), server_default="MEDIUM", nullable=False),
        sa.Column("poll_interval_minutes", sa.Integer(), server_default="720", nullable=False),
        sa.Column("last_poll_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_message", sa.String(length=500), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"source_type IN {_SOURCE_TYPES}", name="ck_external_sources_source_type"),
        sa.CheckConstraint("trust_level IN ('HIGH', 'MEDIUM', 'LOW')", name="ck_external_sources_trust_level"),
        sa.CheckConstraint("poll_interval_minutes > 0", name="ck_external_sources_poll_interval"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_external_sources_id", "external_sources", ["id"])
    op.create_table(
        "external_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("url", sa.String(length=1000), nullable=True),
        sa.Column("canonical_url", sa.String(length=1000), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("author", sa.String(length=255), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("document_type", sa.String(length=32), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"document_type IN {_DOCUMENT_TYPES}", name="ck_external_documents_document_type"),
        sa.ForeignKeyConstraint(["source_id"], ["external_sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_external_documents_id", "external_documents", ["id"])
    op.create_index("ix_external_documents_source_id", "external_documents", ["source_id"])
    op.create_index("ix_external_documents_published_at", "external_documents", ["published_at"])
    op.create_index(
        "uq_external_documents_source_external_id",
        "external_documents",
        ["source_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )
    op.create_index(
        "uq_external_documents_canonical_url",
        "external_documents",
        ["canonical_url"],
        unique=True,
        postgresql_where=sa.text("canonical_url IS NOT NULL"),
    )
    op.create_index(
        "uq_external_documents_content_hash",
        "external_documents",
        ["content_hash"],
        unique=True,
        postgresql_where=sa.text("content_hash IS NOT NULL"),
    )
    op.create_table(
        "document_companies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("relation_type", sa.String(length=16), nullable=False),
        sa.Column("match_method", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"relation_type IN {_RELATIONS}", name="ck_document_companies_relation_type"),
        sa.CheckConstraint("match_method IN ('TICKER', 'COMPANY_NAME', 'ALIAS', 'MANUAL')", name="ck_document_companies_match_method"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 100", name="ck_document_companies_confidence"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["external_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "company_id", "relation_type", name="uq_document_companies_document_company_relation"),
    )
    op.create_index("ix_document_companies_id", "document_companies", ["id"])
    op.create_index("ix_document_companies_document_id", "document_companies", ["document_id"])
    op.create_index("ix_document_companies_company_id", "document_companies", ["company_id"])
    op.create_table(
        "company_aliases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("alias", sa.String(length=255), nullable=False),
        sa.Column("alias_type", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "alias", name="uq_company_aliases_company_alias"),
    )
    op.create_index("ix_company_aliases_id", "company_aliases", ["id"])
    op.create_index("ix_company_aliases_company_id", "company_aliases", ["company_id"])
    op.create_table(
        "intelligence_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("importance", sa.String(length=8), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("source_document_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"event_type IN {_EVENTS}", name="ck_intelligence_events_event_type"),
        sa.CheckConstraint("importance IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')", name="ck_intelligence_events_importance"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 100", name="ck_intelligence_events_confidence"),
        sa.ForeignKeyConstraint(["source_document_id"], ["external_documents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_document_id", "event_type", name="uq_intelligence_events_document_type"),
    )
    op.create_index("ix_intelligence_events_id", "intelligence_events", ["id"])
    op.create_index("ix_intelligence_events_event_date", "intelligence_events", ["event_date"])
    op.create_index("ix_intelligence_events_source_document_id", "intelligence_events", ["source_document_id"])
    op.create_table(
        "event_companies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.CheckConstraint(f"role IN {_RELATIONS}", name="ck_event_companies_role"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 100", name="ck_event_companies_confidence"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["event_id"], ["intelligence_events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "company_id", "role", name="uq_event_companies_event_company_role"),
    )
    op.create_index("ix_event_companies_id", "event_companies", ["id"])
    op.create_index("ix_event_companies_event_id", "event_companies", ["event_id"])
    op.create_index("ix_event_companies_company_id", "event_companies", ["company_id"])


def downgrade() -> None:
    op.drop_table("event_companies")
    op.drop_table("intelligence_events")
    op.drop_table("company_aliases")
    op.drop_table("document_companies")
    op.drop_index("uq_external_documents_content_hash", table_name="external_documents")
    op.drop_index("uq_external_documents_canonical_url", table_name="external_documents")
    op.drop_index("uq_external_documents_source_external_id", table_name="external_documents")
    op.drop_table("external_documents")
    op.drop_table("external_sources")
