from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class MarketPriceRead(BaseModel):
    trade_date: date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    adjusted_close: Decimal | None
    volume: int | None
    source: str

    model_config = ConfigDict(from_attributes=True)


class MarketSnapshotRead(BaseModel):
    company_id: int
    price: Decimal | None
    previous_close: Decimal | None
    market_cap: Decimal | None
    market_cap_source: str | None
    market_cap_method: str | None
    market_cap_as_of: date | None
    market_cap_confidence: str | None
    market_cap_reason: str | None
    currency: str | None
    volume: int | None
    average_volume_20d: int | None
    change_1d_pct: Decimal | None
    change_5d_pct: Decimal | None
    change_1m_pct: Decimal | None
    change_3m_pct: Decimal | None
    change_1y_pct: Decimal | None
    week_52_high: Decimal | None
    week_52_low: Decimal | None
    last_market_date: date | None
    source: str
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
