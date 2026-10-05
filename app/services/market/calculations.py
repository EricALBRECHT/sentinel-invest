"""Snapshot math from daily bars.

Performance uses adjusted_close when the provider sent one, otherwise close.
The 52-week high and low use the traded high and low. This module does not
compute indicators such as RSI, MACD, or moving averages.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from app.services.market.provider import MARKET_SOURCE, DailyBar, MarketQuote

_PERCENT = Decimal("0.0001")
_ONE = Decimal(1)
_SESSIONS_1D = 1
_SESSIONS_5D = 5
_SESSIONS_1M = 21
_SESSIONS_3M = 63
_SESSIONS_1Y = 252
_SESSIONS_52W = 252
_VOLUME_WINDOW = 20


@dataclass(frozen=True)
class SnapshotMetrics:
    price: Decimal | None
    previous_close: Decimal | None
    market_cap: Decimal | None
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
    source: str = MARKET_SOURCE


def build_snapshot(bars: list[DailyBar], quote: MarketQuote | None = None) -> SnapshotMetrics:
    ordered = sorted(bars, key=lambda bar: bar.trade_date)
    latest = ordered[-1] if ordered else None
    previous = ordered[-2] if len(ordered) > 1 else None
    performance = [_performance_price(bar) for bar in ordered]
    window = ordered[-_SESSIONS_52W:]
    highs = [bar.high for bar in window if bar.high is not None]
    lows = [bar.low for bar in window if bar.low is not None]
    supplied = quote or MarketQuote()
    return SnapshotMetrics(
        price=supplied.price if supplied.price is not None else (None if latest is None else latest.close),
        previous_close=supplied.previous_close
        if supplied.previous_close is not None
        else (None if previous is None else previous.close),
        market_cap=supplied.market_cap if supplied.market_cap is not None and supplied.market_cap > 0 else None,
        currency=supplied.currency,
        volume=supplied.volume if supplied.volume is not None else (None if latest is None else latest.volume),
        average_volume_20d=_average_volume(ordered),
        change_1d_pct=_change(performance, _SESSIONS_1D),
        change_5d_pct=_change(performance, _SESSIONS_5D),
        change_1m_pct=_change(performance, _SESSIONS_1M),
        change_3m_pct=_change(performance, _SESSIONS_3M),
        change_1y_pct=_change(performance, _SESSIONS_1Y),
        week_52_high=max(highs) if highs else None,
        week_52_low=min(lows) if lows else None,
        last_market_date=None if latest is None else latest.trade_date,
    )


def _performance_price(bar: DailyBar) -> Decimal | None:
    if bar.adjusted_close is not None:
        return bar.adjusted_close
    return bar.close


def _change(prices: list[Decimal | None], sessions: int) -> Decimal | None:
    if len(prices) <= sessions:
        return None
    latest = prices[-1]
    past = prices[-1 - sessions]
    if latest is None or past is None or past == 0:
        return None
    return ((latest - past) / past * Decimal(100)).quantize(_PERCENT, rounding=ROUND_HALF_UP)


def _average_volume(bars: list[DailyBar]) -> int | None:
    volumes = [bar.volume for bar in bars[-_VOLUME_WINDOW:] if bar.volume is not None]
    if not volumes:
        return None
    average = sum((Decimal(volume) for volume in volumes), Decimal(0)) / Decimal(len(volumes))
    return int(average.quantize(_ONE, rounding=ROUND_HALF_UP))
