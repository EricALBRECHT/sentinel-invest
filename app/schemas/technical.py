from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class TechnicalSnapshotRead(BaseModel):
    company_id: int
    as_of_date: date
    method: str
    price: Decimal | None
    sma_20: Decimal | None
    sma_50: Decimal | None
    sma_100: Decimal | None
    sma_200: Decimal | None
    ema_12: Decimal | None
    ema_26: Decimal | None
    rsi_14: Decimal | None
    macd: Decimal | None
    macd_signal: Decimal | None
    macd_histogram: Decimal | None
    atr_14: Decimal | None
    volatility_20d: Decimal | None
    volume: int | None
    average_volume_20d: int | None
    volume_ratio: Decimal | None
    distance_sma_20_pct: Decimal | None
    distance_sma_50_pct: Decimal | None
    distance_sma_200_pct: Decimal | None
    week_52_position_pct: Decimal | None
    trend_short: str | None
    trend_medium: str | None
    trend_long: str | None
    support_1: Decimal | None
    support_2: Decimal | None
    resistance_1: Decimal | None
    resistance_2: Decimal | None
    technical_score: Decimal | None
    technical_confidence: int
    components_json: dict
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TechnicalChartPointRead(BaseModel):
    trade_date: date
    price: Decimal | None
    sma_20: Decimal | None
    sma_50: Decimal | None
    sma_200: Decimal | None
    volume: int | None
