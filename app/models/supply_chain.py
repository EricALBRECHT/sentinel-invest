"""Directed supply-chain relationships and companies named before they are followed.

A relationship is always source to target. The source plays relationship_type
toward the target. An unknown distinctive name stays a candidate. It does not
become a watched company.
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
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

_TYPES = (
    "('CUSTOMER', 'SUPPLIER', 'PARTNER', 'COMPETITOR', 'INVESTOR', 'SUBCONTRACTOR', "
    "'EQUIPMENT_PROVIDER', 'MATERIAL_PROVIDER', 'INFRASTRUCTURE_PROVIDER', 'OTHER')"
)
_STATUS = "('DISCOVERED', 'CONFIRMED', 'REJECTED', 'STALE')"
_METHODS = "('RULE', 'DOCUMENT', 'MANUAL', 'SEC', 'OTHER')"
_IMPORTANCE = "('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')"
_CANDIDATE_STATUS = "('CANDIDATE', 'VERIFIED', 'IMPORTED', 'REJECTED')"
_ENTITY_TYPES = "('PUBLIC_COMPANY', 'PRIVATE_COMPANY', 'SUBSIDIARY', 'BRAND', 'UNKNOWN')"
_VERIFICATION = "('UNVERIFIED', 'PARTIAL', 'VERIFIED', 'REJECTED')"


def _json_type():
    return JSON().with_variant(JSONB(), "postgresql")


class CompanyRelationship(Base):
    __tablename__ = "company_relationships"
    __table_args__ = (
        UniqueConstraint(
            "source_company_id",
            "target_company_id",
            "relationship_type",
            name="uq_company_relationships_source_target_type",
        ),
        CheckConstraint("source_company_id != target_company_id", name="ck_company_relationships_not_self"),
        CheckConstraint(f"relationship_type IN {_TYPES}", name="ck_company_relationships_type"),
        CheckConstraint("direction IN ('SOURCE_TO_TARGET')", name="ck_company_relationships_direction"),
        CheckConstraint(f"status IN {_STATUS}", name="ck_company_relationships_status"),
        CheckConstraint(f"discovery_method IN {_METHODS}", name="ck_company_relationships_method"),
        CheckConstraint(f"importance IN {_IMPORTANCE}", name="ck_company_relationships_importance"),
        CheckConstraint("confidence >= 0 AND confidence <= 100", name="ck_company_relationships_confidence"),
        CheckConstraint("evidence_count >= 0", name="ck_company_relationships_evidence_count"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    source_company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    relationship_type: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(32), nullable=False, default="SOURCE_TO_TARGET")
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    importance: Mapped[str] = mapped_column(String(16), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DISCOVERED")
    discovery_method: Mapped[str] = mapped_column(String(16), nullable=False, default="RULE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    source_company: Mapped["Company"] = relationship(
        foreign_keys=[source_company_id],
        back_populates="relationships_out",
        lazy="raise",
    )
    target_company: Mapped["Company"] = relationship(
        foreign_keys=[target_company_id],
        back_populates="relationships_in",
        lazy="raise",
    )
    evidences: Mapped[list["RelationshipEvidence"]] = relationship(
        back_populates="relationship",
        lazy="raise",
    )


class RelationshipEvidence(Base):
    __tablename__ = "relationship_evidence"
    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 100", name="ck_relationship_evidence_confidence"),
        Index(
            "uq_relationship_evidence_document",
            "relationship_id",
            "document_id",
            unique=True,
            postgresql_where=text("document_id IS NOT NULL"),
            sqlite_where=text("document_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    relationship_id: Mapped[int] = mapped_column(
        ForeignKey("company_relationships.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("external_documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("intelligence_events.id", ondelete="SET NULL"),
        nullable=True,
    )
    evidence_text: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    relationship: Mapped[CompanyRelationship] = relationship(back_populates="evidences", lazy="raise")


class DiscoveredCompany(Base):
    __tablename__ = "discovered_companies"
    __table_args__ = (
        UniqueConstraint("name", name="uq_discovered_companies_name"),
        CheckConstraint(f"status IN {_CANDIDATE_STATUS}", name="ck_discovered_companies_status"),
        CheckConstraint("confidence >= 0 AND confidence <= 100", name="ck_discovered_companies_confidence"),
        CheckConstraint("evidence_count >= 0", name="ck_discovered_companies_evidence_count"),
        CheckConstraint(f"entity_type IN {_ENTITY_TYPES}", name="ck_discovered_companies_entity_type"),
        CheckConstraint(f"verification_status IN {_VERIFICATION}", name="ck_discovered_companies_verification"),
        CheckConstraint(
            "verification_confidence >= 0 AND verification_confidence <= 100",
            name="ck_discovered_companies_verification_confidence",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ticker: Mapped[str | None] = mapped_column(String(32), nullable=True)
    isin: Mapped[str | None] = mapped_column(String(12), nullable=True)
    country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    exchange: Mapped[str | None] = mapped_column(String(64), nullable=True)
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sec_cik: Mapped[str | None] = mapped_column(String(10), nullable=True)
    entity_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="UNKNOWN",
        server_default="UNKNOWN",
    )
    verification_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="UNVERIFIED",
        server_default="UNVERIFIED",
    )
    verification_confidence: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_evidence_json: Mapped[dict] = mapped_column(_json_type(), nullable=False, default=dict)
    promoted_company_id: Mapped[int | None] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    discovered_from_company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    discovery_reason: Mapped[str] = mapped_column(String(500), nullable=False)
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="CANDIDATE")
    document_ids: Mapped[list] = mapped_column(_json_type(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    discovered_from: Mapped["Company"] = relationship(
        back_populates="discovered_companies",
        foreign_keys=[discovered_from_company_id],
        lazy="raise",
    )
