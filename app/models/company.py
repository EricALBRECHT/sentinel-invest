from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, Numeric, String, false, func, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = (
        CheckConstraint(
            "universe_status IN ('DISCOVERED', 'WATCHED', 'SCREENED', 'DEEP_ANALYSIS', 'PORTFOLIO', 'ARCHIVED')",
            name="ck_companies_universe_status",
        ),
        CheckConstraint(
            "universe_priority >= 0 AND universe_priority <= 100",
            name="ck_companies_universe_priority",
        ),
        CheckConstraint(
            "discovery_source IS NULL OR discovery_source IN "
            "('MANUAL', 'SP500', 'NASDAQ100', 'PEA', 'EUROPE', 'SUPPLY_CHAIN', 'BOTTLENECK', 'INSTITUTIONAL', 'OTHER')",
            name="ck_companies_discovery_source",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    ticker: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    isin: Mapped[str | None] = mapped_column(String(12), unique=True, nullable=True, index=True)
    country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    exchange: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sector: Mapped[str | None] = mapped_column(String(128), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(128), nullable=True)
    market_cap: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    pea_eligible: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=false(),
        default=False,
    )
    sec_cik: Mapped[str | None] = mapped_column(String(10), unique=True, index=True, nullable=True)
    market_symbol: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    universe_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="DISCOVERED",
        server_default="DISCOVERED",
    )
    universe_priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    discovery_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    discovery_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=true(),
    )
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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

    financial_metrics: Mapped[list["FinancialMetric"]] = relationship(
        back_populates="company",
        lazy="raise",
    )
    scores: Mapped[list["CompanyScore"]] = relationship(
        back_populates="company",
        lazy="raise",
    )
    opportunity_profile: Mapped["OpportunityProfile | None"] = relationship(
        back_populates="company",
        lazy="raise",
        uselist=False,
    )
    opportunity_scores: Mapped[list["OpportunityScore"]] = relationship(
        back_populates="company",
        lazy="raise",
    )
    sync_statuses: Mapped[list["CompanySyncStatus"]] = relationship(
        back_populates="company",
        lazy="raise",
    )
    universe_memberships: Mapped[list["UniverseMembership"]] = relationship(
        back_populates="company",
        lazy="raise",
    )
    market_prices: Mapped[list["MarketPrice"]] = relationship(
        back_populates="company",
        lazy="raise",
    )
    market_snapshot: Mapped["CompanyMarketSnapshot | None"] = relationship(
        back_populates="company",
        lazy="raise",
        uselist=False,
    )


from app.models.company_score import CompanyScore  # noqa: E402,F401
from app.models.company_sync_status import CompanySyncStatus  # noqa: E402,F401
from app.models.financial_metric import FinancialMetric  # noqa: E402,F401
from app.models.opportunity_profile import OpportunityProfile  # noqa: E402,F401
from app.models.opportunity_score import OpportunityScore  # noqa: E402,F401
from app.models.universe_membership import UniverseMembership  # noqa: E402,F401
from app.models.market_price import MarketPrice  # noqa: E402,F401
from app.models.company_market_snapshot import CompanyMarketSnapshot  # noqa: E402,F401
