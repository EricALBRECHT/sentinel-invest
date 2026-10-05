from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
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

_TRENDS = "('STRONG_UP', 'UP', 'NEUTRAL', 'DOWN', 'STRONG_DOWN')"


def _json_type():
    return JSON().with_variant(JSONB(), "postgresql")


def _trend_check(column: str) -> CheckConstraint:
    return CheckConstraint(
        f"{column} IS NULL OR {column} IN {_TRENDS}",
        name=f"ck_technical_snapshots_{column}",
    )


class TechnicalSnapshot(Base):
    """One technical timing view per company, session, and method.

    Recalculating the same day updates that row. Older sessions stay.
    """

    __tablename__ = "technical_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "as_of_date",
            "method",
            name="uq_technical_snapshots_company_date_method",
        ),
        _trend_check("trend_short"),
        _trend_check("trend_medium"),
        _trend_check("trend_long"),
        CheckConstraint(
            "technical_score IS NULL OR (technical_score >= 0 AND technical_score <= 100)",
            name="ck_technical_snapshots_score",
        ),
        CheckConstraint(
            "technical_confidence >= 0 AND technical_confidence <= 100",
            name="ck_technical_snapshots_confidence",
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
        default="technical_v1",
        server_default="technical_v1",
    )
    price: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    sma_20: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    sma_50: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    sma_100: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    sma_200: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    ema_12: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    ema_26: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    rsi_14: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    macd: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    macd_signal: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    macd_histogram: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    atr_14: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    volatility_20d: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    average_volume_20d: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    volume_ratio: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    distance_sma_20_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    distance_sma_50_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    distance_sma_200_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    week_52_position_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    trend_short: Mapped[str | None] = mapped_column(String(16), nullable=True)
    trend_medium: Mapped[str | None] = mapped_column(String(16), nullable=True)
    trend_long: Mapped[str | None] = mapped_column(String(16), nullable=True)
    support_1: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    support_2: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    resistance_1: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    resistance_2: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    technical_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    technical_confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
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

    company: Mapped["Company"] = relationship(back_populates="technical_snapshots", lazy="raise")
