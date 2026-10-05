from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _json_type():
    return JSON().with_variant(JSONB(), "postgresql")


class OpportunityProfile(Base):
    __tablename__ = "company_opportunity_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    megatrend_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    market_growth_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    market_size_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    market_penetration_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    strategic_position_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    bottleneck_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    supplier_leverage_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    customer_diversification_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    innovation_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    rd_intensity_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    capacity_expansion_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    geographic_expansion_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    size_runway_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    competitive_moat_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    competition_risk_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    megatrends_json: Mapped[list | None] = mapped_column(_json_type(), nullable=True)
    strategic_roles_json: Mapped[list | None] = mapped_column(_json_type(), nullable=True)
    customers_json: Mapped[list | None] = mapped_column(_json_type(), nullable=True)
    suppliers_json: Mapped[list | None] = mapped_column(_json_type(), nullable=True)
    evidence_json: Mapped[list | None] = mapped_column(_json_type(), nullable=True)
    data_confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    analysis_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    method_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    company: Mapped["Company"] = relationship(back_populates="opportunity_profile", lazy="raise")
