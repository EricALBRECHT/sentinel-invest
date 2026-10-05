from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _json_type():
    return JSON().with_variant(JSONB(), "postgresql")


class OpportunityScore(Base):
    __tablename__ = "company_opportunity_scores"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "score_date",
            "method_version",
            name="uq_company_opportunity_scores_company_date_version",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    score_date: Mapped[date] = mapped_column(Date, nullable=False)
    opportunity_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    opportunity_confidence_score: Mapped[int] = mapped_column(Integer, nullable=False)
    coverage_score: Mapped[int] = mapped_column(Integer, nullable=False)
    coverage_status: Mapped[str] = mapped_column(String(16), nullable=False)
    ranking_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    growth_runway_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    size_runway_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    megatrend_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    market_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    strategic_position_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    bottleneck_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    innovation_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    moat_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    expansion_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    components_json: Mapped[dict] = mapped_column(_json_type(), nullable=False)
    confidence_json: Mapped[dict] = mapped_column(_json_type(), nullable=False)
    method_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    company: Mapped["Company"] = relationship(back_populates="opportunity_scores", lazy="raise")
