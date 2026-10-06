"""Stored AI document analyses with full provenance."""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _json_type():
    return JSON().with_variant(JSONB(), "postgresql")

_STATUSES = "('PENDING', 'RUNNING', 'SUCCESS', 'INVALID_OUTPUT', 'FAILED')"


class AiDocumentAnalysis(Base):
    __tablename__ = "ai_document_analyses"
    __table_args__ = (
        CheckConstraint(f"status IN {_STATUSES}", name="ck_ai_document_analyses_status"),
        CheckConstraint(
            "analysis_confidence IS NULL OR (analysis_confidence >= 0 AND analysis_confidence <= 100)",
            name="ck_ai_document_analyses_confidence",
        ),
        UniqueConstraint(
            "document_id",
            "model_name",
            "prompt_version",
            name="uq_ai_document_analyses_document_model_prompt",
        ),
        Index("ix_ai_document_analyses_status", "status"),
        Index("ix_ai_document_analyses_completed_at", "completed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("external_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING", server_default="PENDING")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[dict | None] = mapped_column(_json_type(), nullable=True)
    raw_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis_confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    worker_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    runtime_json: Mapped[dict | None] = mapped_column(_json_type(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
