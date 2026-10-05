"""Market data contracts.

V1 talks to Yahoo Finance through YahooMarketProvider in client.py.
Sync code depends on this protocol, so a later provider can replace Yahoo
without changing storage or the snapshot math.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol


MARKET_SOURCE = "yahoo"
HISTORY_LOOKBACK_BARS = 320


class MarketProviderError(Exception):
    """The market provider failed before a usable payload was returned."""


@dataclass(frozen=True)
class DailyBar:
    trade_date: date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    adjusted_close: Decimal | None
    volume: int | None


@dataclass(frozen=True)
class MarketQuote:
    price: Decimal | None = None
    previous_close: Decimal | None = None
    market_cap: Decimal | None = None
    currency: str | None = None
    volume: int | None = None
    as_of: date | None = None


class MarketDataProvider(Protocol):
    async def get_history(self, symbol: str, start: date, end: date) -> list[DailyBar]: ...

    async def get_snapshot(self, symbol: str) -> MarketQuote: ...

    async def aclose(self) -> None: ...
