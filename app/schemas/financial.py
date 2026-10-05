from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FiscalPeriod = Literal["FY", "Q1", "Q2", "Q3", "Q4"]


class FinancialMetricRead(BaseModel):
    id: int
    company_id: int
    fiscal_year: int
    fiscal_period: FiscalPeriod
    period_start: date | None
    period_end: date | None
    filed_at: date | None
    revenue: Decimal | None
    gross_profit: Decimal | None
    operating_income: Decimal | None
    net_income: Decimal | None
    eps_basic: Decimal | None
    eps_diluted: Decimal | None
    operating_cash_flow: Decimal | None
    capital_expenditure: Decimal | None
    free_cash_flow: Decimal | None
    cash_and_equivalents: Decimal | None
    total_assets: Decimal | None
    total_liabilities: Decimal | None
    total_debt: Decimal | None
    shareholders_equity: Decimal | None
    shares_outstanding: Decimal | None
    revenue_source_concept: str | None = None
    gross_profit_source_concept: str | None = None
    operating_income_source_concept: str | None = None
    net_income_source_concept: str | None = None
    eps_basic_source_concept: str | None = None
    eps_diluted_source_concept: str | None = None
    operating_cash_flow_source_concept: str | None = None
    capital_expenditure_source_concept: str | None = None
    cash_source_concept: str | None = None
    assets_source_concept: str | None = None
    liabilities_source_concept: str | None = None
    debt_source_concept: str | None = None
    equity_source_concept: str | None = None
    shares_source_concept: str | None = None
    source: str
    source_url: str | None
    filing_type: str | None
    accession_number: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SecSyncRead(BaseModel):
    company_id: int
    ticker: str
    periods_found: int
    created: int
    updated: int
    skipped: int
    deleted: int = 0
    warnings: list[str] = Field(default_factory=list)
