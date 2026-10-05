"""Market capitalization with an explicit source.

A provider figure is used when the quote contains one. Otherwise the cap is
the latest price times the latest shares outstanding from a filing source.
Nothing is estimated when shares are missing, too old, pre-split, or the
price currency is unknown.

SEC EDGAR is the only filing source wired today. registered_share_sources()
is the place to add a European filing source later. A company without that
source's identifier contributes no shares, so the SEC path is not applied
to a listing that does not file there.

Company.market_cap is the figure Size Runway reads, and those bands are in
USD. A LOW confidence figure, or a figure in another currency, stays on the
snapshot and is not copied onto the company.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.financial_metric import FinancialMetric
from app.services.market.calculations import SnapshotMetrics
from app.services.market.provider import MARKET_SOURCE, DailyBar, MarketQuote

METHOD_PROVIDER = "PROVIDER"
METHOD_PRICE_X_SHARES = "PRICE_X_SHARES"
CONFIDENCE_HIGH = "HIGH"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_LOW = "LOW"
SOURCE_CALCULATED = "calculated"
SOURCE_SEC = "sec_edgar"

SHARE_FRESH_DAYS = 120
SHARE_MAX_AGE_DAYS = 450
SPLIT_FACTOR = Decimal("2")
SPLIT_MATCH = Decimal("1.5")
_CAP = Decimal("0.01")
_USD = "USD"


@dataclass(frozen=True)
class ShareCount:
    shares: Decimal
    as_of: date
    source: str
    filed_at: date | None = None


@dataclass(frozen=True)
class MarketCapAssessment:
    market_cap: Decimal | None
    method: str | None
    source: str | None
    as_of: date | None
    confidence: str | None
    currency: str | None
    reason: str
    updates_company: bool
    shares_outstanding: Decimal | None = None


class ShareCountSource(Protocol):
    name: str

    async def load(self, session: AsyncSession, company: Company) -> list[ShareCount]: ...


class SecEdgarShareCountSource:
    """Shares outstanding already stored from SEC company facts.

    The source stays empty unless the company has a CIK. A European listing
    without SEC filings is left for a later source.
    """

    name = SOURCE_SEC

    async def load(self, session: AsyncSession, company: Company) -> list[ShareCount]:
        if not (company.sec_cik or "").strip():
            return []
        statement = select(FinancialMetric).where(
            FinancialMetric.company_id == company.id,
            FinancialMetric.source == SOURCE_SEC,
            FinancialMetric.shares_outstanding.is_not(None),
            FinancialMetric.shares_outstanding > 0,
        )
        rows = (await session.execute(statement)).scalars()
        counts: list[ShareCount] = []
        for row in rows:
            as_of = row.period_end or row.filed_at
            if as_of is None:
                continue
            counts.append(
                ShareCount(
                    shares=Decimal(row.shares_outstanding),
                    as_of=as_of,
                    source=self.name,
                    filed_at=row.filed_at,
                )
            )
        return counts


def registered_share_sources() -> list[ShareCountSource]:
    return [SecEdgarShareCountSource()]


async def resolve_market_cap(
    session: AsyncSession,
    company: Company,
    quote: MarketQuote | None,
    metrics: SnapshotMetrics,
    bars: list[DailyBar],
    sources: list[ShareCountSource] | None = None,
) -> MarketCapAssessment:
    loaded: list[ShareCount] = []
    for source in sources if sources is not None else registered_share_sources():
        loaded.extend(await source.load(session, company))
    return assess_market_cap(
        provider_cap=None if quote is None else quote.market_cap,
        provider_source=MARKET_SOURCE,
        price=metrics.price,
        currency=metrics.currency,
        market_date=metrics.last_market_date,
        shares=loaded,
        bars=bars,
    )


def assess_market_cap(
    *,
    provider_cap: Decimal | None,
    provider_source: str,
    price: Decimal | None,
    currency: str | None,
    market_date: date | None,
    shares: list[ShareCount],
    bars: list[DailyBar],
) -> MarketCapAssessment:
    if provider_cap is not None and provider_cap > 0:
        return _provider_cap(provider_cap, provider_source, currency, market_date)
    return _from_shares(price, currency, market_date, shares, bars)


def _provider_cap(
    provider_cap: Decimal,
    provider_source: str,
    currency: str | None,
    market_date: date | None,
) -> MarketCapAssessment:
    amount = provider_cap.quantize(_CAP)
    if not currency:
        return _result(
            amount,
            METHOD_PROVIDER,
            provider_source,
            market_date,
            CONFIDENCE_LOW,
            None,
            "The provider sent a market cap without a currency.",
            None,
        )
    confidence = CONFIDENCE_HIGH
    reason = f"Market cap {amount} {currency} was supplied by {provider_source}."
    return _result(
        amount,
        METHOD_PROVIDER,
        provider_source,
        market_date,
        confidence,
        currency,
        reason,
        None,
    )


def _from_shares(
    price: Decimal | None,
    currency: str | None,
    market_date: date | None,
    shares: list[ShareCount],
    bars: list[DailyBar],
) -> MarketCapAssessment:
    if not currency:
        return _refused("The market price has no currency, so shares were not multiplied.")
    if price is None or price <= 0 or market_date is None:
        return _refused("No latest market price is available to multiply by shares outstanding.")
    usable = [item for item in shares if item.shares > 0 and item.as_of is not None]
    if not usable:
        return _refused(
            "No shares outstanding from a filing source. "
            "SEC shares are used only when the company has a CIK and a stored SEC figure."
        )
    latest = max(usable, key=lambda item: (item.as_of, item.filed_at or date.min))
    if latest.as_of > market_date:
        return _refused(
            f"The latest share count is dated {latest.as_of.isoformat()}, after the market price."
        )
    age = (market_date - latest.as_of).days
    if age > SHARE_MAX_AGE_DAYS:
        return _refused(
            f"The latest share count is dated {latest.as_of.isoformat()}, "
            f"{age} days before the market price. Counts older than {SHARE_MAX_AGE_DAYS} days are not used."
        )
    if _split_after(bars, latest.as_of, market_date):
        return _refused(
            f"The share count of {latest.shares} as of {latest.as_of.isoformat()} "
            "predates a split in the price history, so it is not on the same basis as the latest price."
        )
    confidence = CONFIDENCE_HIGH if age <= SHARE_FRESH_DAYS else CONFIDENCE_MEDIUM
    reason = (
        f"Price {price} {currency} on {market_date.isoformat()} times "
        f"{latest.shares} shares from {latest.source} as of {latest.as_of.isoformat()}."
    )
    previous = _previous(usable, latest)
    if previous is not None and _span(latest.shares, previous.shares) >= SPLIT_FACTOR:
        share_span = _span(latest.shares, previous.shares)
        price_change = _factor_ratio(bars, previous.as_of, latest.as_of)
        if price_change is None or _span(share_span, _span_ratio(price_change)) > SPLIT_MATCH:
            confidence = CONFIDENCE_LOW
            reason = (
                f"Shares moved from {previous.shares} on {previous.as_of.isoformat()} "
                f"to {latest.shares} on {latest.as_of.isoformat()} without a matching split "
                "in the price history. The product is kept as low confidence and is not stored "
                "on the company."
            )
    amount = (price * latest.shares).quantize(_CAP)
    return _result(
        amount,
        METHOD_PRICE_X_SHARES,
        SOURCE_CALCULATED,
        latest.as_of,
        confidence,
        currency,
        reason,
        latest.shares,
    )


def _result(
    market_cap: Decimal | None,
    method: str | None,
    source: str | None,
    as_of: date | None,
    confidence: str | None,
    currency: str | None,
    reason: str,
    shares: Decimal | None,
) -> MarketCapAssessment:
    accepted = (
        market_cap is not None
        and market_cap > 0
        and confidence in {CONFIDENCE_HIGH, CONFIDENCE_MEDIUM}
        and currency == _USD
    )
    if market_cap is not None and confidence in {CONFIDENCE_HIGH, CONFIDENCE_MEDIUM} and currency not in {None, _USD}:
        reason = f"{reason} Company.market_cap was left unchanged because size runway is denominated in USD."
    return MarketCapAssessment(
        market_cap=market_cap,
        method=method,
        source=source,
        as_of=as_of,
        confidence=confidence,
        currency=currency,
        reason=reason,
        updates_company=accepted,
        shares_outstanding=shares,
    )


def _refused(reason: str) -> MarketCapAssessment:
    return MarketCapAssessment(
        market_cap=None,
        method=None,
        source=None,
        as_of=None,
        confidence=None,
        currency=None,
        reason=reason,
        updates_company=False,
        shares_outstanding=None,
    )


def _previous(shares: list[ShareCount], latest: ShareCount) -> ShareCount | None:
    earlier = [item for item in shares if item.as_of < latest.as_of]
    if not earlier:
        return None
    return max(earlier, key=lambda item: (item.as_of, item.filed_at or date.min))


def _split_after(bars: list[DailyBar], start: date, end: date) -> bool:
    change = _factor_ratio(bars, start, end)
    if change is None:
        return False
    return _span_ratio(change) >= SPLIT_FACTOR


def _factor_ratio(bars: list[DailyBar], start: date, end: date) -> Decimal | None:
    before = _factor_on_or_before(bars, start)
    after = _factor_on_or_before(bars, end)
    if before is None or after is None or after == 0:
        return None
    return before / after


def _factor_on_or_before(bars: list[DailyBar], day: date) -> Decimal | None:
    chosen: Decimal | None = None
    chosen_date: date | None = None
    for bar in bars:
        if bar.trade_date > day:
            continue
        factor = _factor(bar)
        if factor is None:
            continue
        if chosen_date is None or bar.trade_date >= chosen_date:
            chosen = factor
            chosen_date = bar.trade_date
    return chosen


def _factor(bar: DailyBar) -> Decimal | None:
    if bar.close is None or bar.adjusted_close is None or bar.close <= 0 or bar.adjusted_close == 0:
        return None
    return Decimal(bar.close) / Decimal(bar.adjusted_close)


def _span(left: Decimal, right: Decimal) -> Decimal:
    if left <= 0 or right <= 0:
        return Decimal("Infinity")
    return max(left, right) / min(left, right)


def _span_ratio(ratio: Decimal) -> Decimal:
    if ratio <= 0:
        return Decimal("Infinity")
    return max(ratio, Decimal(1) / ratio)
