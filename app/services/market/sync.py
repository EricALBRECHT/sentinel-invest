"""Persist daily bars and refresh the company snapshot.

The first sync asks for ten years. Later syncs start at the last stored
session so a corrected close can replace that row. Old history is kept when
the provider fails.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.company import Company
from app.models.company_market_snapshot import CompanyMarketSnapshot
from app.models.company_sync_status import CompanySyncStatus
from app.models.market_price import MarketPrice
from app.models.opportunity_profile import OpportunityProfile
from app.services.analysis.opportunity_recalculate import recalculate_opportunity_score
from app.services.jobs.errors import brief_error
from app.services.market.calculations import SnapshotMetrics, build_snapshot
from app.services.market.client import build_market_provider
from app.services.market.metadata import MarketCapAssessment, resolve_market_cap
from app.services.market.provider import HISTORY_LOOKBACK_BARS, MARKET_SOURCE, DailyBar
from app.services.market.symbols import resolve_provider_symbol
from app.services.universe.manager import recalculate_universe_priority

logger = logging.getLogger("sentinel.market")

_CAP = Decimal("0.01")


async def execute_market_sync(session: AsyncSession, company_id: int, *, provider=None) -> dict:
    company = await session.get(Company, company_id)
    if company is None:
        return {"company_id": company_id, "symbol": None, "sync": "missing_company"}
    symbol = (company.market_symbol or "").strip()
    if not symbol:
        await _record_failure(session, company_id, "Company has no market symbol")
        return _summary(company_id, None, "skipped_no_symbol", 0, 0, 0, False, False, False, None)
    provider_symbol = await resolve_provider_symbol(session, company, settings.market_provider)
    if not provider_symbol:
        provider_symbol = symbol

    await _mark_attempt(session, company_id)
    created_provider = None
    if provider is None:
        provider = build_market_provider()
        created_provider = provider
    try:
        last_date = await _last_trade_date(session, company.id)
        end = datetime.now(timezone.utc).date()
        start = last_date if last_date is not None else _years_ago(end, settings.market_history_years)
        bars = await provider.get_history(provider_symbol, start, end)
        quote = await provider.get_snapshot(provider_symbol)
        if not bars and last_date is None:
            raise RuntimeError("No market history returned")
        created, updated = await _upsert(session, company.id, bars)
        stored = await _stored_bars(session, company.id)
        metrics = build_snapshot(stored, quote)
        assessment = await resolve_market_cap(session, company, quote, metrics, stored)
        await _save_snapshot(session, company.id, metrics, assessment)
        cap_changed = _apply_market_cap(company, assessment)
        has_profile = await _has_profile(session, company.id)
        opportunity = False
        priority = False
        if cap_changed and has_profile:
            await recalculate_opportunity_score(session, company)
            opportunity = True
            await recalculate_universe_priority(session, company)
            priority = True
        await session.commit()
        await _record_success(session, company.id, created, updated, metrics)
    except Exception as exc:
        await session.rollback()
        await _record_failure(session, company_id, brief_error(exc))
        raise
    finally:
        if created_provider is not None:
            await created_provider.aclose()
    return _summary(
        company.id,
        symbol,
        "success",
        len(bars),
        created,
        updated,
        cap_changed,
        opportunity,
        priority,
        None if metrics.last_market_date is None else metrics.last_market_date.isoformat(),
    )


async def _upsert(session: AsyncSession, company_id: int, bars: list[DailyBar]) -> tuple[int, int]:
    created = 0
    updated = 0
    for bar in bars:
        if bar.close is None:
            continue
        statement = select(MarketPrice).where(
            MarketPrice.company_id == company_id,
            MarketPrice.trade_date == bar.trade_date,
            MarketPrice.source == MARKET_SOURCE,
        )
        row = (await session.execute(statement)).scalar_one_or_none()
        if row is None:
            session.add(
                MarketPrice(
                    company_id=company_id,
                    trade_date=bar.trade_date,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    adjusted_close=bar.adjusted_close,
                    volume=bar.volume,
                    source=MARKET_SOURCE,
                )
            )
            created += 1
            continue
        changed = _assign_bar(row, bar)
        if changed:
            row.updated_at = datetime.now(timezone.utc)
            updated += 1
    await session.flush()
    return created, updated


def _assign_bar(row: MarketPrice, bar: DailyBar) -> bool:
    fields = {
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "adjusted_close": bar.adjusted_close,
        "volume": bar.volume,
    }
    changed = False
    for name, value in fields.items():
        if getattr(row, name) != value:
            setattr(row, name, value)
            changed = True
    return changed


async def _stored_bars(session: AsyncSession, company_id: int) -> list[DailyBar]:
    statement = (
        select(MarketPrice)
        .where(MarketPrice.company_id == company_id, MarketPrice.source == MARKET_SOURCE)
        .order_by(MarketPrice.trade_date.desc())
        .limit(HISTORY_LOOKBACK_BARS)
    )
    rows = list((await session.execute(statement)).scalars())
    rows.reverse()
    return [
        DailyBar(
            trade_date=row.trade_date,
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            adjusted_close=row.adjusted_close,
            volume=row.volume,
        )
        for row in rows
    ]


async def _save_snapshot(
    session: AsyncSession,
    company_id: int,
    metrics: SnapshotMetrics,
    assessment: MarketCapAssessment,
) -> None:
    statement = select(CompanyMarketSnapshot).where(CompanyMarketSnapshot.company_id == company_id)
    row = (await session.execute(statement)).scalar_one_or_none()
    if row is None:
        row = CompanyMarketSnapshot(company_id=company_id, source=MARKET_SOURCE)
        session.add(row)
    row.price = metrics.price
    row.previous_close = metrics.previous_close
    row.market_cap = assessment.market_cap
    row.market_cap_source = assessment.source
    row.market_cap_method = assessment.method
    row.market_cap_as_of = assessment.as_of
    row.market_cap_confidence = assessment.confidence
    row.market_cap_reason = assessment.reason[:500]
    if metrics.currency:
        row.currency = metrics.currency
    row.volume = metrics.volume
    row.average_volume_20d = metrics.average_volume_20d
    row.change_1d_pct = metrics.change_1d_pct
    row.change_5d_pct = metrics.change_5d_pct
    row.change_1m_pct = metrics.change_1m_pct
    row.change_3m_pct = metrics.change_3m_pct
    row.change_1y_pct = metrics.change_1y_pct
    row.week_52_high = metrics.week_52_high
    row.week_52_low = metrics.week_52_low
    row.last_market_date = metrics.last_market_date
    row.source = MARKET_SOURCE
    row.updated_at = datetime.now(timezone.utc)


def _apply_market_cap(company: Company, assessment: MarketCapAssessment) -> bool:
    if not assessment.updates_company or assessment.market_cap is None or assessment.market_cap <= 0:
        return False
    normalized = assessment.market_cap.quantize(_CAP)
    current = None if company.market_cap is None else Decimal(company.market_cap).quantize(_CAP)
    if current == normalized:
        return False
    company.market_cap = normalized
    company.updated_at = datetime.now(timezone.utc)
    return True


async def _has_profile(session: AsyncSession, company_id: int) -> bool:
    statement = select(OpportunityProfile.id).where(OpportunityProfile.company_id == company_id)
    return (await session.execute(statement)).scalar_one_or_none() is not None


async def _last_trade_date(session: AsyncSession, company_id: int) -> date | None:
    statement = select(MarketPrice.trade_date).where(
        MarketPrice.company_id == company_id,
        MarketPrice.source == MARKET_SOURCE,
    ).order_by(MarketPrice.trade_date.desc()).limit(1)
    return (await session.execute(statement)).scalar_one_or_none()


async def _mark_attempt(session: AsyncSession, company_id: int) -> None:
    row = await _status_row(session, company_id)
    row.last_attempt_at = datetime.now(timezone.utc)
    await session.commit()


async def _record_success(
    session: AsyncSession,
    company_id: int,
    created: int,
    updated: int,
    metrics: SnapshotMetrics,
) -> None:
    row = await _status_row(session, company_id)
    row.last_success_at = datetime.now(timezone.utc)
    row.last_error_at = None
    row.last_error_message = None
    row.consecutive_failures = 0
    row.last_result_json = {
        "created": created,
        "updated": updated,
        "last_market_date": None if metrics.last_market_date is None else metrics.last_market_date.isoformat(),
    }
    await session.commit()


async def _record_failure(session: AsyncSession, company_id: int, message: str) -> None:
    company = await session.get(Company, company_id)
    if company is None:
        return
    row = await _status_row(session, company_id)
    row.last_error_at = datetime.now(timezone.utc)
    row.last_error_message = message[:300]
    row.consecutive_failures = int(row.consecutive_failures or 0) + 1
    await session.commit()


async def _status_row(session: AsyncSession, company_id: int) -> CompanySyncStatus:
    statement = select(CompanySyncStatus).where(
        CompanySyncStatus.company_id == company_id,
        CompanySyncStatus.source == MARKET_SOURCE,
    )
    row = (await session.execute(statement)).scalar_one_or_none()
    if row is None:
        row = CompanySyncStatus(company_id=company_id, source=MARKET_SOURCE, consecutive_failures=0)
        session.add(row)
        await session.flush()
    return row


def _years_ago(end: date, years: int) -> date:
    try:
        return end.replace(year=end.year - years)
    except ValueError:
        return end.replace(year=end.year - years, day=28)


def _summary(
    company_id: int,
    symbol: str | None,
    sync: str,
    bars: int,
    created: int,
    updated: int,
    market_cap_updated: bool,
    opportunity_recalculated: bool,
    priority_recalculated: bool,
    last_trade_date: str | None,
) -> dict:
    return {
        "company_id": company_id,
        "symbol": symbol,
        "sync": sync,
        "bars": bars,
        "created": created,
        "updated": updated,
        "market_cap_updated": market_cap_updated,
        "opportunity_recalculated": opportunity_recalculated,
        "priority_recalculated": priority_recalculated,
        "last_trade_date": last_trade_date,
    }
