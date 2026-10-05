from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
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

_LONG = "('LOW', 'MODERATE', 'GOOD', 'HIGH', 'VERY_HIGH')"
_ENTRY = "('POOR', 'NEUTRAL', 'FAVORABLE', 'STRONG', 'EXTENDED_OR_EXCEPTIONAL')"
_READY = "('INCOMPLETE', 'PARTIAL', 'USABLE', 'COMPLETE')"
_COVERAGE = "('INCOMPLETE', 'PARTIAL', 'USABLE', 'COMPLETE')"


def _json_type():
    return JSON().with_variant(JSONB(), "postgresql")


def _score_check(column: str) -> CheckConstraint:
    return CheckConstraint(
        f"{column} IS NULL OR ({column} >= 0 AND {column} <= 100)",
        name=f"ck_investment_views_{column}",
    )


def _confidence_check(column: str) -> CheckConstraint:
    return CheckConstraint(
        f"{column} IS NULL OR ({column} >= 0 AND {column} <= 100)",
        name=f"ck_investment_views_{column}",
    )


class InvestmentView(Base):
    """One composite view per company, date, and method.

    Recalculating the same day updates that row. Older dates stay.
    The row keeps three readings. It is not a buy or sell decision.
    """

    __tablename__ = "company_investment_views"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "as_of_date",
            "method",
            name="uq_company_investment_views_company_date_method",
        ),
        _score_check("quality_score"),
        _score_check("opportunity_score"),
        _score_check("technical_score"),
        _score_check("long_term_conviction_score"),
        _score_check("entry_attractiveness_score"),
        _score_check("analysis_readiness_score"),
        _confidence_check("quality_confidence"),
        _confidence_check("opportunity_confidence"),
        _confidence_check("technical_confidence"),
        CheckConstraint(
            "opportunity_coverage IS NULL OR (opportunity_coverage >= 0 AND opportunity_coverage <= 100)",
            name="ck_investment_views_opportunity_coverage",
        ),
        CheckConstraint(
            f"opportunity_coverage_status IS NULL OR opportunity_coverage_status IN {_COVERAGE}",
            name="ck_investment_views_coverage_status",
        ),
        CheckConstraint(
            f"long_term_conviction_label IS NULL OR long_term_conviction_label IN {_LONG}",
            name="ck_investment_views_conviction_label",
        ),
        CheckConstraint(
            f"entry_attractiveness_label IS NULL OR entry_attractiveness_label IN {_ENTRY}",
            name="ck_investment_views_entry_label",
        ),
        CheckConstraint(
            f"analysis_readiness_label IS NULL OR analysis_readiness_label IN {_READY}",
            name="ck_investment_views_readiness_label",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    method: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="investment_view_v1",
        server_default="investment_view_v1",
    )
    quality_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    quality_confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    opportunity_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    opportunity_confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    opportunity_coverage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    opportunity_coverage_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    opportunity_ranking_eligible: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    technical_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    technical_confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    long_term_conviction_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    entry_attractiveness_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    analysis_readiness_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    long_term_conviction_label: Mapped[str | None] = mapped_column(String(16), nullable=True)
    entry_attractiveness_label: Mapped[str | None] = mapped_column(String(32), nullable=True)
    analysis_readiness_label: Mapped[str | None] = mapped_column(String(16), nullable=True)
    components_json: Mapped[dict] = mapped_column(_json_type(), nullable=False)
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

    company: Mapped["Company"] = relationship(back_populates="investment_views", lazy="raise")
