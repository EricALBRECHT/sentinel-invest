"""Build and store technical snapshots.

The daily job records the latest session only. backfill_technical_history
can rebuild earlier sessions, and each session sees only prices on or before
its own date.
"""

from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.market_price import MarketPrice
from app.models.technical_snapshot import TechnicalSnapshot
from app.services.market.provider import MARKET_SOURCE
from app.services.technical.indicators import PriceBar, chart_rows, compute_indicators
from app.services.technical.levels import nearest_levels
from app.services.technical.scoring import score_snapshot
from app.services.technical.trends import classify_long, classify_medium, classify_short

TECHNICAL_METHOD = "technical_v1"


async def recalculate_technical_snapshot(session: AsyncSession, company_id: int) -> TechnicalSnapshot | None:
    """Score the latest stored session and upsert that date only."""
    company = await session.get(Company, company_id)
    if company is None:
        return None
    bars = await _bars(session, company_id)
    if not bars:
        return None
    row, _created = await _persist(session, company_id, bars)
    await session.commit()
    await session.refresh(row)
    return row


async def backfill_technical_history(
    session: AsyncSession,
    company_id: int,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict:
    """Score each selected session with prices known on that session.

    This is not scheduled. A full history has to be requested explicitly.
    """
    company = await session.get(Company, company_id)
    if company is None:
        return _backfill_summary(company_id, "missing_company", 0, 0, 0)
    bars = await _bars(session, company_id)
    selected = [
        index
        for index, bar in enumerate(bars)
        if (start_date is None or bar.trade_date >= start_date) and (end_date is None or bar.trade_date <= end_date)
    ]
    if not selected:
        return _backfill_summary(company_id, "no_history", 0, 0, 0)
    created = 0
    updated = 0
    for index in selected:
        _row, is_new = await _persist(session, company_id, bars[: index + 1])
        if is_new:
            created += 1
        else:
            updated += 1
    await session.commit()
    return _backfill_summary(company_id, "success", created, updated, len(selected))


async def chart_data(session: AsyncSession, company_id: int, limit: int) -> list[dict] | None:
    company = await session.get(Company, company_id)
    if company is None:
        return None
    return chart_rows(await _bars(session, company_id), limit)


async def _persist(session: AsyncSession, company_id: int, bars: list[PriceBar]) -> tuple[TechnicalSnapshot, bool]:
    indicators = compute_indicators(bars)
    trend_short = classify_short(indicators.performance, indicators.sma_20, indicators.sma_20_five_ago)
    trend_medium = classify_medium(indicators.performance, indicators.sma_20, indicators.sma_50)
    trend_long = classify_long(indicators.performance, indicators.sma_50, indicators.sma_200)
    support_1, support_2, resistance_1, resistance_2 = nearest_levels(
        [bar.high for bar in bars],
        [bar.low for bar in bars],
        indicators.close,
    )
    scored = score_snapshot(
        indicators,
        trend_short=trend_short,
        trend_medium=trend_medium,
        trend_long=trend_long,
        support_1=support_1,
        resistance_1=resistance_1,
    )
    row, created = await _upsert(session, company_id, indicators.as_of_date)
    row.price = indicators.price
    row.sma_20 = indicators.sma_20
    row.sma_50 = indicators.sma_50
    row.sma_100 = indicators.sma_100
    row.sma_200 = indicators.sma_200
    row.ema_12 = indicators.ema_12
    row.ema_26 = indicators.ema_26
    row.rsi_14 = indicators.rsi_14
    row.macd = indicators.macd
    row.macd_signal = indicators.macd_signal
    row.macd_histogram = indicators.macd_histogram
    row.atr_14 = indicators.atr_14
    row.volatility_20d = indicators.volatility_20d
    row.volume = indicators.volume
    row.average_volume_20d = indicators.average_volume_20d
    row.volume_ratio = indicators.volume_ratio
    row.distance_sma_20_pct = indicators.distance_sma_20_pct
    row.distance_sma_50_pct = indicators.distance_sma_50_pct
    row.distance_sma_200_pct = indicators.distance_sma_200_pct
    row.week_52_position_pct = indicators.week_52_position_pct
    row.trend_short = trend_short
    row.trend_medium = trend_medium
    row.trend_long = trend_long
    row.support_1 = support_1
    row.support_2 = support_2
    row.resistance_1 = resistance_1
    row.resistance_2 = resistance_2
    row.technical_score = scored.technical_score
    row.technical_confidence = scored.technical_confidence
    row.components_json = scored.components
    row.updated_at = datetime.now(timezone.utc)
    return row, created


async def _upsert(session: AsyncSession, company_id: int, as_of_date: date) -> tuple[TechnicalSnapshot, bool]:
    statement = select(TechnicalSnapshot).where(
        TechnicalSnapshot.company_id == company_id,
        TechnicalSnapshot.as_of_date == as_of_date,
        TechnicalSnapshot.method == TECHNICAL_METHOD,
    )
    row = (await session.execute(statement)).scalar_one_or_none()
    if row is not None:
        return row, False
    row = TechnicalSnapshot(
        company_id=company_id,
        as_of_date=as_of_date,
        method=TECHNICAL_METHOD,
        technical_confidence=0,
        components_json={},
    )
    session.add(row)
    return row, True


async def _bars(session: AsyncSession, company_id: int, end_date: date | None = None) -> list[PriceBar]:
    statement = (
        select(MarketPrice)
        .where(MarketPrice.company_id == company_id, MarketPrice.source == MARKET_SOURCE)
        .order_by(MarketPrice.trade_date.asc())
    )
    if end_date is not None:
        statement = statement.where(MarketPrice.trade_date <= end_date)
    rows = (await session.execute(statement)).scalars()
    bars: list[PriceBar] = []
    for row in rows:
        performance = row.adjusted_close if row.adjusted_close is not None else row.close
        if performance is None:
            continue
        bars.append(
            PriceBar(
                trade_date=row.trade_date,
                close=row.close,
                high=row.high,
                low=row.low,
                performance=performance,
                volume=row.volume,
            )
        )
    return bars


def _backfill_summary(company_id: int, status: str, created: int, updated: int, dates: int) -> dict:
    return {
        "company_id": company_id,
        "status": status,
        "method": TECHNICAL_METHOD,
        "created": created,
        "updated": updated,
        "dates": dates,
    }
