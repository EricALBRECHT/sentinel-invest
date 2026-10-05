"""Market sync stays offline. Yahoo is parsed from a fixture, and queue tests use Redis db 15."""

from datetime import date, timedelta
from decimal import Decimal
import inspect

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.jobs import scheduler
from app.jobs.market_sync import enqueue_selected_market
from app.jobs.queues import enqueue_market_sync, market_job_id, market_retry, redis_connection
from app.jobs.worker import main as worker_main
from app.models.company import Company
from app.models.market_price import MarketPrice
from app.models.opportunity_profile import OpportunityProfile
from app.models.opportunity_score import OpportunityScore
from app.services.market.calculations import build_snapshot
from app.services.market.client import _bars, _quote
from app.services.market.provider import DailyBar, MarketProviderError, MarketQuote
from app.services.market.sync import execute_market_sync
from app.services.universe.manager import add_company_to_universe
from tests.test_quality_score import _headers


@pytest.fixture
def job_redis(monkeypatch):
    monkeypatch.setattr(settings, "redis_db", 15)
    connection = redis_connection()
    connection.flushdb()
    yield connection
    connection.flushdb()
    connection.close()


def _bar(day: int, price: int, volume: int) -> DailyBar:
    amount = Decimal(price)
    return DailyBar(
        trade_date=date(2024, 1, 1) + timedelta(days=day),
        open=amount,
        high=amount + 2,
        low=amount - 1,
        close=amount + 1,
        adjusted_close=amount,
        volume=volume,
    )


def _series(count: int) -> list[DailyBar]:
    return [_bar(index, index + 1, 100 + index) for index in range(count)]


class FakeMarket:
    def __init__(self, bars: list[DailyBar], quote: MarketQuote, error: Exception | None = None) -> None:
        self.bars = bars
        self.quote = quote
        self.error = error
        self.starts: list[date] = []

    async def get_history(self, symbol: str, start: date, end: date) -> list[DailyBar]:
        if self.error is not None:
            raise self.error
        self.starts.append(start)
        return [bar for bar in self.bars if start <= bar.trade_date <= end]

    async def get_snapshot(self, symbol: str) -> MarketQuote:
        if self.error is not None:
            raise self.error
        return self.quote

    async def aclose(self) -> None:
        return None


def test_snapshot_uses_adjusted_close_and_traded_range():
    short = _series(6)
    metrics = build_snapshot(short, MarketQuote(price=Decimal("9"), currency="EUR"))
    assert metrics.change_1d_pct == Decimal("20.0000")
    assert metrics.change_5d_pct == Decimal("500.0000")
    assert metrics.change_1m_pct is None
    assert metrics.change_3m_pct is None
    assert metrics.change_1y_pct is None
    assert metrics.average_volume_20d == 103
    assert metrics.week_52_high == Decimal("8")
    assert metrics.week_52_low == Decimal("0")
    assert metrics.price == Decimal("9")

    long = _series(253)
    long_metrics = build_snapshot(long, None)
    latest = Decimal(253)
    assert long_metrics.change_1d_pct == ((latest - Decimal(252)) / Decimal(252) * 100).quantize(Decimal("0.0001"))
    assert long_metrics.change_5d_pct == ((latest - Decimal(248)) / Decimal(248) * 100).quantize(Decimal("0.0001"))
    assert long_metrics.change_1m_pct == ((latest - Decimal(232)) / Decimal(232) * 100).quantize(Decimal("0.0001"))
    assert long_metrics.change_3m_pct == ((latest - Decimal(190)) / Decimal(190) * 100).quantize(Decimal("0.0001"))
    assert long_metrics.change_1y_pct == ((latest - Decimal(1)) / Decimal(1) * 100).quantize(Decimal("0.0001"))
    assert long_metrics.week_52_high == Decimal(255)
    assert long_metrics.week_52_low == Decimal(1)
    assert long_metrics.average_volume_20d == 343


def test_yahoo_payload_keeps_adjusted_close_and_market_cap():
    chart = {
        "timestamp": [1700000000],
        "indicators": {
            "quote": [{"open": [1.5], "high": [2], "low": [1], "close": [1.8], "volume": [100]}],
            "adjclose": [{"adjclose": [1.25]}],
        },
        "meta": {
            "currency": "usd",
            "regularMarketPrice": 1.8,
            "previousClose": 1.7,
            "regularMarketVolume": 90,
            "marketCap": 2_000_000_000,
        },
    }
    parsed = _bars(chart)
    quote = _quote(chart)
    assert parsed[0].close == Decimal("1.800000")
    assert parsed[0].adjusted_close == Decimal("1.250000")
    assert parsed[0].volume == 100
    assert quote.currency == "USD"
    assert quote.market_cap == Decimal("2000000000.00")
    assert quote.price == Decimal("1.800000")


async def test_sync_upserts_history_updates_cap_and_recalculates_opportunity(session_factory):
    quote = MarketQuote(
        price=Decimal("6"),
        previous_close=Decimal("5"),
        market_cap=Decimal("2000000000"),
        currency="USD",
        volume=60,
    )
    bars = _series(6)
    provider = FakeMarket(bars, quote)
    async with session_factory() as session:
        company = Company(name="Market Corp", ticker="MKTC", market_symbol="MKTC")
        session.add(company)
        await session.commit()
        await session.refresh(company)
        session.add(
            OpportunityProfile(
                company_id=company.id,
                method_version="opportunity_v1",
                market_growth_score=Decimal("50"),
            )
        )
        await session.commit()
        first = await execute_market_sync(session, company.id, provider=provider)
        await session.refresh(company)
        stored = await session.scalar(select(func.count()).select_from(MarketPrice))
        score = (
            await session.execute(select(OpportunityScore).where(OpportunityScore.company_id == company.id))
        ).scalar_one()
        bars[-1] = _bar(5, 9, 70)
        provider.bars = bars
        second = await execute_market_sync(session, company.id, provider=provider)
        await session.refresh(company)
        stored_again = await session.scalar(select(func.count()).select_from(MarketPrice))

    assert first["sync"] == "success"
    assert first["created"] == 6
    assert first["updated"] == 0
    assert first["market_cap_updated"] is True
    assert first["opportunity_recalculated"] is True
    assert first["priority_recalculated"] is True
    assert stored == 6
    assert company.market_cap == Decimal("2000000000.00")
    assert score.size_runway_score == Decimal("100.00")
    assert second["created"] == 0
    assert second["updated"] == 1
    assert second["market_cap_updated"] is False
    assert second["opportunity_recalculated"] is False
    assert stored_again == 6
    assert provider.starts[1] == bars[-2].trade_date or provider.starts[1] == date(2024, 1, 6)


async def test_sync_failure_keeps_prices_and_skips_without_symbol(session_factory):
    async with session_factory() as session:
        company = Company(name="Fail Market", ticker="FAILM", market_symbol="FAILM")
        bare = Company(name="No Symbol", ticker="NOSYM")
        session.add_all([company, bare])
        await session.commit()
        await session.refresh(company)
        session.add(
            MarketPrice(
                company_id=company.id,
                trade_date=date(2024, 1, 1),
                close=Decimal("10"),
                source="yahoo",
            )
        )
        await session.commit()
        company_id = company.id
        bare_id = bare.id
        with pytest.raises(MarketProviderError):
            await execute_market_sync(
                session,
                company_id,
                provider=FakeMarket([], MarketQuote(), error=MarketProviderError("provider down")),
            )
        remaining = await session.scalar(
            select(func.count()).select_from(MarketPrice).where(MarketPrice.company_id == company_id)
        )
        skipped = await execute_market_sync(session, bare_id, provider=FakeMarket([], MarketQuote()))

    assert remaining == 1
    assert skipped["sync"] == "skipped_no_symbol"
    assert skipped["opportunity_recalculated"] is False


async def test_market_routes_symbol_history_and_admin(client, session_factory, job_redis, monkeypatch):
    monkeypatch.setattr(settings, "market_sync_max_companies_per_run", 2)
    for path, method in (
        ("/admin/jobs/market-sync/1", client.post),
        ("/admin/jobs/market-sync-due", client.post),
        ("/companies/1/market/history", client.get),
        ("/companies/1/market/snapshot", client.get),
    ):
        assert (await method(path)).status_code == 401

    headers = await _headers(client, email="market@example.com")
    created = await client.post(
        "/companies",
        json={"name": "Route Market", "ticker": "RMKT"},
        headers=headers,
    )
    company_id = created.json()["id"]
    missing_symbol = await client.post(f"/admin/jobs/market-sync/{company_id}", headers=headers)
    renamed = await client.patch(
        f"/companies/{company_id}",
        json={"market_symbol": "stm.pa"},
        headers=headers,
    )
    first = await client.post(f"/admin/jobs/market-sync/{company_id}", headers=headers)
    second = await client.post(f"/admin/jobs/market-sync/{company_id}", headers=headers)

    async with session_factory() as session:
        company = await session.get(Company, company_id)
        watched = Company(
            name="Watched Market",
            ticker="WTCH",
            market_symbol="WTCH",
            universe_priority=90,
            universe_status="WATCHED",
        )
        deep = Company(
            name="Deep Market",
            ticker="DEEP",
            market_symbol="DEEP",
            universe_status="DEEP_ANALYSIS",
        )
        portfolio = Company(
            name="Portfolio Market",
            ticker="PORT",
            market_symbol="PORT",
            universe_status="PORTFOLIO",
        )
        session.add_all([watched, deep, portfolio])
        await session.commit()
        for member, status_name in (
            (watched, "WATCHED"),
            (deep, "DEEP_ANALYSIS"),
            (portfolio, "PORTFOLIO"),
        ):
            await add_company_to_universe(
                session,
                member.id,
                "MANUAL",
                "MANUAL",
                universe_status=status_name,
            )
        due = await enqueue_selected_market(session)
        provider = FakeMarket(_series(6), MarketQuote(market_cap=Decimal("3000000000"), currency="USD"))
        await execute_market_sync(session, company.id, provider=provider)

    history = await client.get(
        f"/companies/{company_id}/market/history",
        params={"date_from": "2024-01-03", "limit": 2},
        headers=headers,
    )
    snapshot = await client.get(f"/companies/{company_id}/market/snapshot", headers=headers)
    admin = await client.get("/admin/status", headers=headers)

    assert missing_symbol.status_code == 400
    assert renamed.json()["market_symbol"] == "STM.PA"
    assert first.status_code == 202
    assert first.json()["job_id"] == market_job_id(company_id)
    assert second.status_code == 200
    assert second.json()["enqueued"] is False
    assert due["enqueued"] == 2
    assert due["job_ids"][0] == market_job_id(portfolio.id)
    assert due["job_ids"][1] == market_job_id(deep.id)
    assert history.status_code == 200
    assert len(history.json()) == 2
    assert history.json()[0]["adjusted_close"] is not None
    assert snapshot.status_code == 200
    assert snapshot.json()["currency"] == "USD"
    assert snapshot.json()["change_5d_pct"] == "500.0000"
    body = admin.json()["market"]
    assert body["companies_with_market_data"] == 1
    assert body["companies_without_market_data"] == 3
    assert body["market_jobs_due"] >= 1
    assert body["last_market_sync"]
    assert company.market_symbol == "STM.PA"


def test_market_scheduler_enqueues_without_fetching(monkeypatch):
    calls = {}

    def fake():
        calls["enqueued"] = True
        return {"selected": 0, "enqueued": 0, "already_active": 0, "job_ids": []}

    monkeypatch.setattr(scheduler, "enqueue_due_market_syncs", fake)
    assert scheduler.run_market_scan()["enqueued"] == 0
    assert calls["enqueued"] is True
    source = inspect.getsource(scheduler)
    assert "sync_company_market" not in source
    assert "get_history" not in source
    worker_source = inspect.getsource(worker_main)
    assert worker_source.index("QUEUE_MARKET") < worker_source.index("QUEUE_SEC")
    assert market_retry().max == 2
    assert enqueue_market_sync
