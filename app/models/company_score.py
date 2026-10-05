from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _json_type():
    return JSON().with_variant(JSONB(), "postgresql")


class CompanyScore(Base):
    __tablename__ = "company_scores"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "score_date",
            "method_version",
            name="uq_company_scores_company_date_version",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    score_date: Mapped[date] = mapped_column(Date, nullable=False)
    quality_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    overall_confidence_score: Mapped[int] = mapped_column(Integer, nullable=False)
    revenue_growth_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    profit_growth_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    fcf_growth_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    margins_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    profitability_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    debt_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    cash_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    dilution_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    stability_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    metrics_json: Mapped[dict] = mapped_column(_json_type(), nullable=False)
    confidence_json: Mapped[dict] = mapped_column(_json_type(), nullable=False)
    method_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    company: Mapped["Company"] = relationship(back_populates="scores", lazy="raise")
