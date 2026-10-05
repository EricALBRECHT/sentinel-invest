"""Technical timing stays offline. Prices are local rows and jobs use Redis db 15."""

from datetime import date, timedelta
from decimal import Decimal
import inspect

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.jobs import scheduler
from app.jobs.market_sync import sync_company_market
from app.jobs.queues import (
    enqueue_technical,
    enqueue_technical_backfill,
    redis_connection,
    technical_backfill_job_id,
    technical_job_id,
)
from app.jobs.technical import schedule_technical_after_market
from app.services.technical.service import TECHNICAL_METHOD, backfill_technical_history
from app.models.company import Company
from app.models.market_price import MarketPrice
from app.models.technical_snapshot import TechnicalSnapshot
from app.services.market.provider import MARKET_SOURCE
from app.services.technical.indicators import (
    PriceBar,
    annualized_volatility,
    atr_wilder,
    chart_rows,
    compute_indicators,
    ema_last,
    macd_last,
    rsi_wilder,
    sma_last,
)
from app.services.technical.levels import nearest_levels
from app.services.technical.scoring import WEIGHTS, bounded_score, score_snapshot
from app.services.technical.service import recalculate_technical_snapshot
from app.services.technical.trends import STRONG_DOWN, STRONG_UP, classify_long, classify_medium, classify_short
from tests.test_quality_score import _headers


@pytest.fixture
def job_redis(monkeypatch):
    monkeypatch.setattr(settings, "redis_db", 15)
    connection = redis_connection()
    connection.flushdb()
    yield connection
    connection.flushdb()
    connection.close()


def _bar(day: int, close: str, volume: int = 10, high: str | None = None, low: str | None = None) -> PriceBar:
    price = Decimal(close)
    return PriceBar(
        trade_date=date(2024, 1, 1) + timedelta(days=day),
        close=price,
        high=price + 1 if high is None else Decimal(high),
        low=price - 1 if low is None else Decimal(low),
        performance=price,
        volume=volume,
    )


def _prices(count: int, start: int = 1) -> list[Decimal]:
    return [Decimal(start + index) for index in range(count)]


def _indicators(**overrides):
    from app.services.technical.indicators import IndicatorSet

    base = dict(
        as_of_date=date(2026, 10, 5),
        sessions=260,
        price=Decimal("110"),
        performance=Decimal("110"),
        previous_performance=Decimal("100"),
        close=Decimal("110"),
        sma_20=Decimal("105"),
        sma_50=Decimal("100"),
        sma_100=Decimal("95"),
        sma_200=Decimal("90"),
        sma_20_five_ago=Decimal("100"),
        ema_12=Decimal("108"),
        ema_26=Decimal("104"),
        rsi_14=Decimal("50"),
        macd=Decimal("1"),
        macd_signal=Decimal("0.4"),
        macd_histogram=Decimal("0.6"),
        atr_14=Decimal("2"),
        volatility_20d=Decimal("20"),
        volume=150,
        average_volume_20d=100,
        volume_ratio=Decimal("1.5"),
        distance_sma_20_pct=Decimal("4"),
        distance_sma_50_pct=Decimal("10"),
        distance_sma_200_pct=Decimal("10"),
        week_52_position_pct=Decimal("80"),
    )
    base.update(overrides)
    return IndicatorSet(**base)


def _scored(**overrides):
    indicators = overrides.pop("indicators", _indicators())
    return score_snapshot(
        indicators,
        trend_short=overrides.get("trend_short", STRONG_UP),
        trend_medium=overrides.get("trend_medium", STRONG_UP),
        trend_long=overrides.get("trend_long", STRONG_UP),
        support_1=overrides.get("support_1", Decimal("100")),
        resistance_1=overrides.get("resistance_1", Decimal("140")),
    )


def test_sma_ema_rsi_and_macd_use_the_documented_windows():
    values = [Decimal(item) for item in (1, 2, 3, 4, 5)]
    assert sma_last(values, 3) == Decimal(4)
    assert ema_last(values, 3) == Decimal(4)
    assert rsi_wilder(_prices(14)) is None
    assert rsi_wilder(_prices(15)) == Decimal(100)
    assert rsi_wilder(list(reversed(_prices(15)))) == Decimal(0)
    flat = [Decimal(10)] * 40
    macd_value, signal, histogram = macd_last(flat)
    assert macd_value == Decimal(0)
    assert signal == Decimal(0)
    assert histogram == Decimal(0)
    assert macd_last(_prices(30)) == (None, None, None)


def test_atr_volatility_and_volume_ratio():
    bars = [_bar(index, str(10 + index), volume=10 if index < 19 else 20) for index in range(20)]
    assert atr_wilder(bars) == Decimal(2)
    assert annualized_volatility([Decimal(100)] * 21) == Decimal(0)
    returns = [Decimal("0.01") if index % 2 == 0 else Decimal("-0.01") for index in range(20)]
    prices = [Decimal(100)]
    for item in returns:
        prices.append(prices[-1] * (1 + item))
    mean = sum(returns, Decimal(0)) / Decimal(20)
    variance = sum((item - mean) ** 2 for item in returns) / Decimal(19)
    expected = variance.sqrt() * Decimal(252).sqrt() * Decimal(100)
    assert annualized_volatility(prices) == expected
    indicators = compute_indicators(bars)
    assert indicators.average_volume_20d == 11
    assert indicators.volume_ratio == (Decimal(20) / Decimal(11)).quantize(Decimal("0.0001"))
    assert indicators.volatility_20d is None
    assert annualized_volatility([Decimal(100)] * 20) is None
    adjusted = [
        PriceBar(
            trade_date=date(2024, 1, 1) + timedelta(days=index),
            close=Decimal(10),
            high=Decimal(11),
            low=Decimal(9),
            performance=Decimal(8),
            volume=10,
        )
        for index in range(20)
    ]
    assert compute_indicators(adjusted).sma_20 == Decimal("8.000000")


def test_trends_and_pivot_levels():
    assert classify_short(Decimal(110), Decimal(100), Decimal(90)) == STRONG_UP
    assert classify_medium(Decimal(110), Decimal(105), Decimal(100)) == STRONG_UP
    assert classify_long(Decimal(80), Decimal(90), Decimal(100)) == STRONG_DOWN
    assert classify_long(Decimal(80), Decimal(90), None) is None
    highs = [Decimal(105)] * 20
    lows = [Decimal(95)] * 20
    highs[4] = Decimal(130)
    highs[12] = Decimal(115)
    lows[6] = Decimal(80)
    lows[14] = Decimal(70)
    support_1, support_2, resistance_1, resistance_2 = nearest_levels(highs, lows, Decimal(100))
    assert support_1 == Decimal("80.000000")
    assert support_2 == Decimal("70.000000")
    assert resistance_1 == Decimal("115.000000")
    assert resistance_2 == Decimal("130.000000")
    assert nearest_levels(highs[:5], lows[:5], Decimal(100)) == (None, None, None, None)


def test_score_stays_inside_bounds_and_penalizes_poor_timing():
    assert sum(WEIGHTS.values()) == Decimal(100)
    assert bounded_score(Decimal("140")) == Decimal("100.00")
    assert bounded_score(Decimal("-3")) == Decimal("0.00")
    healthy = _scored()
    overbought = _scored(indicators=_indicators(rsi_14=Decimal("85")))
    below = _scored(
        indicators=_indicators(distance_sma_200_pct=Decimal("-8")),
        trend_long=STRONG_DOWN,
        trend_medium=STRONG_DOWN,
        trend_short=STRONG_DOWN,
    )
    near_resistance = _scored(support_1=Decimal("90"), resistance_1=Decimal("111"))
    near_support = _scored(support_1=Decimal("108"), resistance_1=Decimal("140"))
    assert healthy.technical_score is not None
    assert Decimal(0) <= healthy.technical_score <= Decimal(100)
    assert overbought.technical_score < healthy.technical_score
    assert below.technical_score < healthy.technical_score
    assert Decimal(near_resistance.components["support_resistance"]["points"]) < Decimal(
        near_support.components["support_resistance"]["points"]
    )
    assert healthy.technical_confidence == 100
    partial = score_snapshot(
        _indicators(sessions=30, sma_200=None, macd=None, macd_signal=None, distance_sma_200_pct=None),
        trend_short=STRONG_UP,
        trend_medium=None,
        trend_long=None,
        support_1=None,
        resistance_1=None,
    )
    assert partial.technical_confidence == 33
    assert partial.components["trend_long"]["included"] is False
    assert "not a buy or sell" in partial.components["note"]


def test_chart_rows_leave_long_averages_empty_until_the_window_is_full():
    bars = [_bar(index, str(index + 1), volume=5) for index in range(25)]
    rows = chart_rows(bars, limit=5)
    assert len(rows) == 5
    assert rows[-1]["sma_20"] == sma_last([bar.performance for bar in bars], 20).quantize(Decimal("0.000001"))
    assert rows[-1]["sma_200"] is None
    assert rows[-1]["volume"] == 5


async def test_short_history_stores_null_score_and_recalculation_keeps_one_row(session_factory):
    async with session_factory() as session:
        company = Company(name="Short Technical", ticker="SHORT")
        session.add(company)
        await session.commit()
        await session.refresh(company)
        session.add_all(_stored(company.id, 10))
        await session.commit()
        short = await recalculate_technical_snapshot(session, company.id)
        assert short is not None
        assert short.technical_score is None
        assert short.rsi_14 is None
        assert short.sma_20 is None
        assert short.sma_200 is None
        assert short.technical_confidence < 100
        session.add_all(_stored(company.id, 40, offset=10))
        await session.commit()
        longer = await recalculate_technical_snapshot(session, company.id)
        count = await session.scalar(select(func.count()).select_from(TechnicalSnapshot))
    assert longer is not None
    assert longer.method == "technical_v1"
    assert longer.sma_20 == Decimal("10.000000")
    assert longer.rsi_14 == Decimal("50.0000")
    assert longer.sma_200 is None
    assert longer.macd == Decimal("0.000000")
    assert longer.technical_score is not None
    assert longer.technical_confidence < 100
    assert short.as_of_date != longer.as_of_date
    assert count == 2


def test_technical_job_is_not_duplicated(job_redis):
    first = enqueue_technical(7)
    second = enqueue_technical(7)
    assert first["job_id"] == technical_job_id(7)
    assert first["queue"] == "analysis"
    assert first["enqueued"] is True
    assert second["enqueued"] is False
    assert second["job_id"] == first["job_id"]


def test_successful_market_sync_schedules_technical(monkeypatch):
    calls = []

    def fake(company_id):
        calls.append(company_id)
        return {"enqueued": True, "status": "queued", "job_id": technical_job_id(company_id)}

    monkeypatch.setattr("app.jobs.technical.enqueue_technical", fake)
    assert schedule_technical_after_market({"sync": "skipped_no_symbol", "company_id": 1}) is False
    assert calls == []
    assert schedule_technical_after_market({"sync": "success", "company_id": 4}) is True
    assert calls == [4]
    assert "schedule_technical_after_market" in inspect.getsource(sync_company_market)


def test_unchanged_market_sync_does_not_schedule_technical(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.jobs.technical.enqueue_technical",
        lambda company_id: calls.append(company_id) or {"enqueued": True, "status": "queued"},
    )
    unchanged = {"sync": "success", "company_id": 4, "created": 0, "updated": 0}
    changed = {"sync": "success", "company_id": 4, "created": 1, "updated": 0}
    assert schedule_technical_after_market(unchanged) is False
    assert schedule_technical_after_market(changed) is True
    assert calls == [4]
    assert "backfill_technical" not in inspect.getsource(scheduler)


def test_technical_backfill_job_is_not_duplicated(job_redis):
    first = enqueue_technical_backfill(8, "2026-01-01", "2026-01-31")
    second = enqueue_technical_backfill(8, "2026-02-01", None)
    assert first["job_id"] == technical_backfill_job_id(8)
    assert first["queue"] == "analysis"
    assert first["enqueued"] is True
    assert second["enqueued"] is False
    assert second["job_id"] == first["job_id"]


async def test_history_keeps_days_and_backfill_ignores_future_prices(session_factory):
    async with session_factory() as session:
        company = Company(name="History Technical", ticker="HIST")
        session.add(company)
        await session.commit()
        await session.refresh(company)
        session.add_all(_stored(company.id, 30))
        spike_date = date(2024, 1, 1) + timedelta(days=30)
        session.add(
            MarketPrice(
                company_id=company.id,
                trade_date=spike_date,
                open=Decimal(500),
                high=Decimal(510),
                low=Decimal(490),
                close=Decimal(500),
                adjusted_close=Decimal(500),
                volume=100,
                source=MARKET_SOURCE,
            )
        )
        await session.commit()
        cutoff = date(2024, 1, 1) + timedelta(days=29)
        first = await backfill_technical_history(session, company.id, end_date=cutoff)
        second = await backfill_technical_history(session, company.id, end_date=cutoff)
        early = await session.scalar(
            select(TechnicalSnapshot).where(
                TechnicalSnapshot.company_id == company.id,
                TechnicalSnapshot.as_of_date == cutoff,
            )
        )
        latest = await recalculate_technical_snapshot(session, company.id)
        await session.refresh(early)
        count = await session.scalar(
            select(func.count()).select_from(TechnicalSnapshot).where(TechnicalSnapshot.company_id == company.id)
        )
        same_day = await recalculate_technical_snapshot(session, company.id)
        count_again = await session.scalar(
            select(func.count()).select_from(TechnicalSnapshot).where(TechnicalSnapshot.company_id == company.id)
        )

    assert first["status"] == "success"
    assert first["created"] == 30
    assert first["dates"] == 30
    assert second["created"] == 0
    assert second["updated"] == 30
    assert early.price == Decimal("10.000000")
    assert early.sma_20 == Decimal("10.000000")
    assert early.method == TECHNICAL_METHOD
    assert latest.as_of_date == spike_date
    assert latest.price == Decimal("500.000000")
    assert early.price == Decimal("10.000000")
    assert count == 31
    assert same_day.id == latest.id
    assert count_again == 31


async def test_technical_history_filters_and_backfill_route(client, session_factory, job_redis):
    assert (await client.post("/admin/jobs/technical-backfill/1")).status_code == 401
    headers = await _headers(client, email="history@example.com")
    created = await client.post(
        "/companies",
        json={"name": "Filter Technical", "ticker": "FILT"},
        headers=headers,
    )
    company_id = created.json()["id"]
    async with session_factory() as session:
        session.add_all(_stored(company_id, 4))
        await session.commit()
        await backfill_technical_history(session, company_id)
        session.add(
            TechnicalSnapshot(
                company_id=company_id,
                as_of_date=date(2024, 1, 4),
                method="technical_v0",
                technical_confidence=0,
                components_json={},
            )
        )
        await session.commit()
    history = await client.get(f"/companies/{company_id}/technical/history", headers=headers)
    latest = await client.get(f"/companies/{company_id}/technical/latest", headers=headers)
    window = await client.get(
        f"/companies/{company_id}/technical/history",
        params={"date_from": "2024-01-02", "date_to": "2024-01-03"},
        headers=headers,
    )
    other = await client.get(
        f"/companies/{company_id}/technical/history",
        params={"method": "technical_v0"},
        headers=headers,
    )
    queued = await client.post(
        f"/admin/jobs/technical-backfill/{company_id}",
        params={"start_date": "2024-01-01", "end_date": "2024-01-04"},
        headers=headers,
    )
    dates = [row["as_of_date"] for row in history.json()]

    assert dates == ["2024-01-04", "2024-01-03", "2024-01-02", "2024-01-01"]
    assert all(row["method"] == "technical_v1" for row in history.json())
    assert latest.json()["as_of_date"] == "2024-01-04"
    assert latest.json()["method"] == "technical_v1"
    assert [row["as_of_date"] for row in window.json()] == ["2024-01-03", "2024-01-02"]
    assert len(other.json()) == 1
    assert other.json()[0]["method"] == "technical_v0"
    assert queued.status_code == 202
    assert queued.json()["job_id"] == technical_backfill_job_id(company_id)


async def test_technical_routes_require_jwt_and_admin_counts_scores(client, session_factory):
    for path, method in (
        ("/companies/1/technical/recalculate", client.post),
        ("/companies/1/technical/latest", client.get),
        ("/companies/1/technical/history", client.get),
        ("/companies/1/technical/chart-data", client.get),
    ):
        assert (await method(path)).status_code == 401

    headers = await _headers(client, email="technical@example.com")
    created = await client.post(
        "/companies",
        json={"name": "Technical Route", "ticker": "TECH"},
        headers=headers,
    )
    company_id = created.json()["id"]
    missing = await client.post(f"/companies/{company_id}/technical/recalculate", headers=headers)
    async with session_factory() as session:
        session.add_all(_stored(company_id, 25))
        await session.commit()
    recalculated = await client.post(f"/companies/{company_id}/technical/recalculate", headers=headers)
    latest = await client.get(f"/companies/{company_id}/technical/latest", headers=headers)
    history = await client.get(f"/companies/{company_id}/technical/history", headers=headers)
    chart = await client.get(
        f"/companies/{company_id}/technical/chart-data",
        params={"limit": 10},
        headers=headers,
    )
    admin = await client.get("/admin/status", headers=headers)
    body = recalculated.json()

    assert missing.status_code == 404
    assert recalculated.status_code == 200
    assert body["sma_20"] is not None
    assert body["technical_score"] is not None
    assert "trend_long" in body["components_json"]
    assert latest.status_code == 200
    assert latest.json()["method"] == "technical_v1"
    assert latest.json()["as_of_date"] == body["as_of_date"]
    assert history.status_code == 200
    assert len(history.json()) == 1
    assert chart.status_code == 200
    assert len(chart.json()) == 10
    assert chart.json()[-1]["sma_20"] is not None
    technical = admin.json()["technical"]
    assert technical["companies_scored"] == 1
    assert technical["companies_without_score"] == 0
    assert technical["technical_snapshots_count"] == 1
    assert technical["oldest_technical_date"] == technical["latest_technical_date"]
    assert technical["last_calculation"]


def _stored(company_id: int, count: int, offset: int = 0) -> list[MarketPrice]:
    rows = []
    for index in range(offset, offset + count):
        close = Decimal(10)
        rows.append(
            MarketPrice(
                company_id=company_id,
                trade_date=date(2024, 1, 1) + timedelta(days=index),
                open=close,
                high=close + 1,
                low=close - 1,
                close=close,
                adjusted_close=close,
                volume=100,
                source=MARKET_SOURCE,
            )
        )
    return rows
