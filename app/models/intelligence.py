"""External documents and the events extracted from them.

Documents keep useful text, a URL, a hash, and provenance. Raw HTML is not stored.
A later purge can drop the text and keep the hash, the URL, and the event row.
"""

from datetime import date, datetime
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

_SOURCE_TYPES = (
    "('NEWS', 'COMPANY_IR', 'REGULATOR', 'SEC', 'RSS', 'MARKET', 'INDUSTRY', 'GOVERNMENT', 'OTHER')"
)
_TRUST = "('HIGH', 'MEDIUM', 'LOW')"
_DOCUMENT_TYPES = (
    "('NEWS_ARTICLE', 'PRESS_RELEASE', 'SEC_FILING', 'BLOG_POST', "
    "'GOVERNMENT_RELEASE', 'INDUSTRY_ARTICLE', 'OTHER')"
)
_RELATIONS = "('SUBJECT', 'MENTION', 'CUSTOMER', 'SUPPLIER', 'PARTNER', 'COMPETITOR', 'INVESTOR', 'OTHER')"
_MATCH = "('TICKER', 'COMPANY_NAME', 'ALIAS', 'MANUAL')"
_EVENTS = (
    "('CONTRACT', 'PARTNERSHIP', 'ACQUISITION', 'INVESTMENT', 'NEW_FACTORY', "
    "'CAPACITY_EXPANSION', 'PRODUCT_LAUNCH', 'CUSTOMER_WIN', 'SUPPLIER_CHANGE', "
    "'REGULATORY', 'MANAGEMENT', 'FINANCING', 'LAYOFF', 'CYBERSECURITY', 'LEGAL', 'OTHER')"
)
_IMPORTANCE = "('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')"


def _json_type():
    return JSON().with_variant(JSONB(), "postgresql")


class ExternalSource(Base):
    __tablename__ = "external_sources"
    __table_args__ = (
        CheckConstraint(f"source_type IN {_SOURCE_TYPES}", name="ck_external_sources_source_type"),
        CheckConstraint(f"trust_level IN {_TRUST}", name="ck_external_sources_trust_level"),
        CheckConstraint("poll_interval_minutes > 0", name="ck_external_sources_poll_interval"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    base_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    trust_level: Mapped[str] = mapped_column(String(8), nullable=False, default="MEDIUM", server_default="MEDIUM")
    poll_interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=720, server_default="720")
    last_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(_json_type(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    documents: Mapped[list["ExternalDocument"]] = relationship(back_populates="source", lazy="raise")


class ExternalDocument(Base):
    __tablename__ = "external_documents"
    __table_args__ = (
        CheckConstraint(f"document_type IN {_DOCUMENT_TYPES}", name="ck_external_documents_document_type"),
        Index(
            "uq_external_documents_source_external_id",
            "source_id",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
            sqlite_where=text("external_id IS NOT NULL"),
        ),
        Index(
            "uq_external_documents_canonical_url",
            "canonical_url",
            unique=True,
            postgresql_where=text("canonical_url IS NOT NULL"),
            sqlite_where=text("canonical_url IS NOT NULL"),
        ),
        Index(
            "uq_external_documents_content_hash",
            "content_hash",
            unique=True,
            postgresql_where=text("content_hash IS NOT NULL"),
            sqlite_where=text("content_hash IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("external_sources.id", ondelete="CASCADE"), nullable=False, index=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    canonical_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    document_type: Mapped[str] = mapped_column(String(32), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(_json_type(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    source: Mapped[ExternalSource] = relationship(back_populates="documents", lazy="raise")
    company_links: Mapped[list["DocumentCompany"]] = relationship(back_populates="document", lazy="raise")
    events: Mapped[list["IntelligenceEvent"]] = relationship(back_populates="source_document", lazy="raise")


class DocumentCompany(Base):
    __tablename__ = "document_companies"
    __table_args__ = (
        UniqueConstraint("document_id", "company_id", "relation_type", name="uq_document_companies_document_company_relation"),
        CheckConstraint(f"relation_type IN {_RELATIONS}", name="ck_document_companies_relation_type"),
        CheckConstraint(f"match_method IN {_MATCH}", name="ck_document_companies_match_method"),
        CheckConstraint("confidence >= 0 AND confidence <= 100", name="ck_document_companies_confidence"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("external_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    relation_type: Mapped[str] = mapped_column(String(16), nullable=False)
    match_method: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    document: Mapped[ExternalDocument] = relationship(back_populates="company_links", lazy="raise")
    company: Mapped["Company"] = relationship(back_populates="document_links", lazy="raise")


class CompanyAlias(Base):
    __tablename__ = "company_aliases"
    __table_args__ = (
        UniqueConstraint("company_id", "alias", name="uq_company_aliases_company_alias"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    alias: Mapped[str] = mapped_column(String(255), nullable=False)
    alias_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    company: Mapped["Company"] = relationship(back_populates="aliases", lazy="raise")


class IntelligenceEvent(Base):
    __tablename__ = "intelligence_events"
    __table_args__ = (
        UniqueConstraint("source_document_id", "event_type", name="uq_intelligence_events_document_type"),
        CheckConstraint(f"event_type IN {_EVENTS}", name="ck_intelligence_events_event_type"),
        CheckConstraint(f"importance IN {_IMPORTANCE}", name="ck_intelligence_events_importance"),
        CheckConstraint("confidence >= 0 AND confidence <= 100", name="ck_intelligence_events_confidence"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    event_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    importance: Mapped[str] = mapped_column(String(8), nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    source_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("external_documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    source_document: Mapped[ExternalDocument | None] = relationship(back_populates="events", lazy="raise")
    company_links: Mapped[list["EventCompany"]] = relationship(back_populates="event", lazy="raise")


class CompanyExternalSource(Base):
    __tablename__ = "company_external_sources"
    __table_args__ = (
        UniqueConstraint("company_id", "source_id", name="uq_company_external_sources_company_source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("external_sources.id", ondelete="CASCADE"), nullable=False, index=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class EventCompany(Base):
    __tablename__ = "event_companies"
    __table_args__ = (
        UniqueConstraint("event_id", "company_id", "role", name="uq_event_companies_event_company_role"),
        CheckConstraint(f"role IN {_RELATIONS}", name="ck_event_companies_role"),
        CheckConstraint("confidence >= 0 AND confidence <= 100", name="ck_event_companies_confidence"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("intelligence_events.id", ondelete="CASCADE"), nullable=False, index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)

    event: Mapped[IntelligenceEvent] = relationship(back_populates="company_links", lazy="raise")
    company: Mapped["Company"] = relationship(back_populates="event_links", lazy="raise")
