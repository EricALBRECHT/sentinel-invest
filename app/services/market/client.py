"""Yahoo Finance chart client, the V1 MarketDataProvider.

Sentinel calls the public chart endpoint used by Yahoo's own pages:

    https://query1.finance.yahoo.com/v8/finance/chart/{symbol}

Daily bars include adjusted close, so splits are not recomputed here.
A second small chart request reads the latest quote and market cap from the
same payload when Yahoo includes them. Requests are spaced by
MARKET_MIN_INTERVAL_SECONDS and time out after MARKET_TIMEOUT_SECONDS.
This is an unofficial source. It can change or refuse anonymous clients.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
import asyncio
import logging
import time
from urllib.parse import quote

import httpx

from app.core.config import settings
from app.services.market.provider import DailyBar, MarketProviderError, MarketQuote

logger = logging.getLogger("sentinel.market")

_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_RETRYABLE = frozenset({429, 500, 502, 503, 504})


class YahooMarketProvider:
    def __init__(
        self,
        *,
        user_agent: str,
        timeout: float,
        max_retries: int,
        min_interval_seconds: float,
    ) -> None:
        self._max_retries = max(1, max_retries)
        self._min_interval = max(0.0, min_interval_seconds)
        self._next_request = 0.0
        self._pace = asyncio.Lock()
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_history(self, symbol: str, start: date, end: date) -> list[DailyBar]:
        payload = await self._chart(symbol, start, end)
        return _bars(payload)

    async def get_snapshot(self, symbol: str) -> MarketQuote:
        end = datetime.now(timezone.utc).date()
        start = end.fromordinal(end.toordinal() - 7)
        payload = await self._chart(symbol, start, end)
        return _quote(payload)

    async def _chart(self, symbol: str, start: date, end: date) -> dict:
        url = _CHART_URL.format(symbol=quote(symbol, safe=""))
        period1 = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
        period2 = int(datetime(end.year, end.month, end.day, 23, 59, tzinfo=timezone.utc).timestamp())
        params = {
            "period1": period1,
            "period2": period2,
            "interval": "1d",
            "includeAdjustedClose": "true",
            "events": "history",
        }
        payload = await self._get_json(url, params)
        chart = payload.get("chart") if isinstance(payload, dict) else None
        result = chart.get("result") if isinstance(chart, dict) else None
        if not result:
            raise MarketProviderError("Yahoo returned no chart for this symbol")
        return result[0]

    async def _get_json(self, url: str, params: dict) -> dict:
        last_status = None
        for attempt in range(self._max_retries):
            await self._wait()
            try:
                response = await self._client.get(url, params=params)
            except httpx.HTTPError as exc:
                logger.warning("market_request_failed error_type=%s", type(exc).__name__)
                if attempt + 1 == self._max_retries:
                    raise MarketProviderError("Market data request failed") from exc
                await asyncio.sleep(min(30, 2**attempt))
                continue
            if response.status_code == 200:
                try:
                    body = response.json()
                except ValueError as exc:
                    raise MarketProviderError("Market data response was not JSON") from exc
                if isinstance(body, dict):
                    return body
                raise MarketProviderError("Market data response was not JSON")
            last_status = response.status_code
            logger.warning("market_request_rejected status_code=%s", response.status_code)
            if response.status_code not in _RETRYABLE or attempt + 1 == self._max_retries:
                break
            await asyncio.sleep(min(30, 2**attempt))
        raise MarketProviderError(f"Market data request failed with status {last_status}")

    async def _wait(self) -> None:
        async with self._pace:
            now = time.monotonic()
            if now < self._next_request:
                await asyncio.sleep(self._next_request - now)
            self._next_request = time.monotonic() + self._min_interval


def build_market_provider() -> YahooMarketProvider:
    if settings.market_provider != "yahoo":
        raise MarketProviderError("Unsupported market data provider")
    return YahooMarketProvider(
        user_agent=settings.market_user_agent,
        timeout=settings.market_timeout_seconds,
        max_retries=settings.market_max_retries,
        min_interval_seconds=settings.market_min_interval_seconds,
    )


def _bars(chart: dict) -> list[DailyBar]:
    timestamps = chart.get("timestamp") or []
    indicators = chart.get("indicators") or {}
    quote = (indicators.get("quote") or [{}])[0]
    adjusted = (indicators.get("adjclose") or [{}])[0].get("adjclose") or []
    bars: list[DailyBar] = []
    for index, stamp in enumerate(timestamps):
        close = _at(quote.get("close"), index)
        if close is None:
            continue
        bars.append(
            DailyBar(
                trade_date=datetime.fromtimestamp(int(stamp), timezone.utc).date(),
                open=_money(_at(quote.get("open"), index)),
                high=_money(_at(quote.get("high"), index)),
                low=_money(_at(quote.get("low"), index)),
                close=_money(close),
                adjusted_close=_money(_at(adjusted, index)),
                volume=_volume(_at(quote.get("volume"), index)),
            )
        )
    return bars


def _quote(chart: dict) -> MarketQuote:
    meta = chart.get("meta") or {}
    as_of = meta.get("regularMarketTime")
    market_cap = _cap(meta.get("marketCap"))
    return MarketQuote(
        price=_money(meta.get("regularMarketPrice")),
        previous_close=_money(meta.get("chartPreviousClose") or meta.get("previousClose")),
        market_cap=market_cap,
        currency=_text(meta.get("currency")),
        volume=_volume(meta.get("regularMarketVolume")),
        as_of=None if as_of is None else datetime.fromtimestamp(int(as_of), timezone.utc).date(),
    )


def _at(values, index: int):
    if not isinstance(values, list) or index >= len(values):
        return None
    return values[index]


def _money(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(format(value, "f")).quantize(Decimal("0.000001"))
    except (ValueError, ArithmeticError):
        return None


def _cap(value) -> Decimal | None:
    amount = _money(value)
    if amount is None or amount <= 0:
        return None
    return amount.quantize(Decimal("0.01"))


def _volume(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _text(value) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip().upper()
    return cleaned or None
