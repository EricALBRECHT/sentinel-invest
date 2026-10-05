from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

FISCAL_PERIODS = ("FY", "Q1", "Q2", "Q3", "Q4")


class FinancialMetric(Base):
    __tablename__ = "financial_metrics"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "fiscal_year",
            "fiscal_period",
            "source",
            name="uq_financial_metrics_company_period_source",
        ),
        CheckConstraint(
            "fiscal_period IN ('FY', 'Q1', 'Q2', 'Q3', 'Q4')",
            name="ck_financial_metrics_fiscal_period",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    fiscal_period: Mapped[str] = mapped_column(String(2), nullable=False)
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    filed_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    revenue: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    gross_profit: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    operating_income: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    net_income: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    eps_basic: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    eps_diluted: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    operating_cash_flow: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    capital_expenditure: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    free_cash_flow: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    cash_and_equivalents: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    total_assets: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    total_liabilities: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    total_debt: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    shareholders_equity: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    shares_outstanding: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    revenue_source_concept: Mapped[str | None] = mapped_column(String(160), nullable=True)
    gross_profit_source_concept: Mapped[str | None] = mapped_column(String(160), nullable=True)
    operating_income_source_concept: Mapped[str | None] = mapped_column(String(160), nullable=True)
    net_income_source_concept: Mapped[str | None] = mapped_column(String(160), nullable=True)
    eps_basic_source_concept: Mapped[str | None] = mapped_column(String(160), nullable=True)
    eps_diluted_source_concept: Mapped[str | None] = mapped_column(String(160), nullable=True)
    operating_cash_flow_source_concept: Mapped[str | None] = mapped_column(String(160), nullable=True)
    capital_expenditure_source_concept: Mapped[str | None] = mapped_column(String(160), nullable=True)
    cash_source_concept: Mapped[str | None] = mapped_column(String(160), nullable=True)
    assets_source_concept: Mapped[str | None] = mapped_column(String(160), nullable=True)
    liabilities_source_concept: Mapped[str | None] = mapped_column(String(160), nullable=True)
    debt_source_concept: Mapped[str | None] = mapped_column(String(160), nullable=True)
    equity_source_concept: Mapped[str | None] = mapped_column(String(160), nullable=True)
    shares_source_concept: Mapped[str | None] = mapped_column(String(160), nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    filing_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    accession_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
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

    company: Mapped["Company"] = relationship(back_populates="financial_metrics", lazy="raise")
