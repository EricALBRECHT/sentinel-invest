from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class CompanyMarketSnapshot(Base):
    """One current market view per company, derived from daily history."""

    __tablename__ = "company_market_snapshots"
    __table_args__ = (
        CheckConstraint(
            "market_cap_method IS NULL OR market_cap_method IN ('PROVIDER', 'PRICE_X_SHARES')",
            name="ck_company_market_snapshots_cap_method",
        ),
        CheckConstraint(
            "market_cap_confidence IS NULL OR market_cap_confidence IN ('HIGH', 'MEDIUM', 'LOW')",
            name="ck_company_market_snapshots_cap_confidence",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    price: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    previous_close: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    market_cap: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    market_cap_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    market_cap_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    market_cap_as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    market_cap_confidence: Mapped[str | None] = mapped_column(String(8), nullable=True)
    market_cap_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    average_volume_20d: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    change_1d_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    change_5d_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    change_1m_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    change_3m_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    change_1y_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    week_52_high: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    week_52_low: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    last_market_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    company: Mapped["Company"] = relationship(back_populates="market_snapshot", lazy="raise")
