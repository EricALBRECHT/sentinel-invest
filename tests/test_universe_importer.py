"""Universe index import and progressive bootstrap stay offline in tests."""

import inspect
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.jobs import scheduler
from app.jobs.queues import (
    enqueue_universe_bootstrap,
    enqueue_universe_members_refresh,
    redis_connection,
)
from app.models.company import Company
from app.models.company_sync_status import CompanySyncStatus
from app.models.universe_membership import UniverseMembership
from app.services.market.provider import MARKET_SOURCE
from app.services.universe.bootstrap import bootstrap_universe_data, universe_market_status
from app.services.universe.providers.base import NormalizedMember
from app.services.universe.providers.http import WikipediaClient, find_table, html_tables
from app.services.universe.providers.nasdaq100 import Nasdaq100Provider
from app.services.universe.providers.sp500 import Sp500Provider
from app.services.universe import refresh as refresh_service
from app.services.universe.rules import status_after_add
from tests.test_quality_score import _headers

_SP500_HTML = """
<html><body>
<table>
<tr><th>Symbol</th><th>Security</th><th>GICS Sector</th><th>GICS Sub-Industry</th><th>CIK</th></tr>
<tr><td>NVDA</td><td>NVIDIA Corporation</td><td>Information Technology</td><td>Semiconductors</td><td>1045810</td></tr>
<tr><td>AAPL</td><td>Apple Inc.</td><td>Information Technology</td><td>Technology Hardware</td><td>320193</td></tr>
<tr><td>MSFT</td><td>Microsoft Corporation</td><td>Information Technology</td><td>Systems Software</td><td>789019</td></tr>
<tr><td>OLD</td><td>Old Index Corp</td><td>Financials</td><td>Banks</td><td>1111111</td></tr>
</table>
</body></html>
"""

_NASDAQ_HTML = """
<html><body>
<table>
<tr><th>Ticker</th><th>Company</th><th>GICS Sector</th><th>GICS Sub-Industry</th></tr>
<tr><td>NVDA</td><td>NVIDIA Corporation</td><td>Information Technology</td><td>Semiconductors</td></tr>
<tr><td>TSLA</td><td>Tesla, Inc.</td><td>Consumer Discretionary</td><td>Automobile Manufacturers</td></tr>
<tr><td>AAPL</td><td>Apple Inc.</td><td>Information Technology</td><td>Technology Hardware</td></tr>
</table>
</body></html>
"""


@pytest.fixture
def job_redis(monkeypatch):
    monkeypatch.setattr(settings, "redis_db", 15)
    connection = redis_connection()
    connection.flushdb()
    yield connection
    connection.flushdb()
    connection.close()


def _transport(html_by_page: dict[str, str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page")
        html = html_by_page.get(page or "")
        if html is None:
            return httpx.Response(404, json={"error": "missing"})
        return httpx.Response(200, json={"parse": {"text": {"*": html}}})

    return httpx.MockTransport(handler)


async def _members_from_html(html: str, page: str, *, exchange: str | None = None) -> list[NormalizedMember]:
    client = WikipediaClient(transport=_transport({page: html}), min_interval_seconds=0)
    try:
        rows = find_table(html_tables(await client.parse_html(page)), {"ticker", "name"})
    finally:
        await client.aclose()
    provider = Sp500Provider() if exchange is None else Nasdaq100Provider()
    members = []
    for row in rows:
        members.append(
            provider.normalize_member(
                {
                    "ticker": row["ticker"],
                    "name": row["name"],
                    "country": "US",
                    "exchange": exchange,
                    "sector": row.get("sector"),
                    "industry": row.get("industry"),
                    "sec_cik": row.get("cik"),
                    "market_symbol": row["ticker"],
                    "source_metadata": {"fixture": True},
                }
            )
        )
    return members


class FixtureSp500(Sp500Provider):
    def __init__(self, html: str) -> None:
        super().__init__()
        self._html = html

    async def fetch_members(self) -> list[NormalizedMember]:
        return await _members_from_html(self._html, "List of S&P 500 companies")


class FixtureNasdaq(Nasdaq100Provider):
    def __init__(self, html: str) -> None:
        super().__init__()
        self._html = html

    async def fetch_members(self) -> list[NormalizedMember]:
        return await _members_from_html(self._html, "Nasdaq-100", exchange="NASDAQ")


def test_status_after_add_never_downgrades_watched():
    assert status_after_add("WATCHED", "SCREENED") == "WATCHED"
    assert status_after_add("DISCOVERED", "SCREENED") == "SCREENED"
    assert status_after_add("ARCHIVED", "SCREENED") == "SCREENED"
    assert status_after_add("DISCOVERED", None) == "WATCHED"


_SP500_CSV_HEADER = (
    "Symbol,Security,GICS Sector,GICS Sub-Industry,Headquarters Location,Date added,CIK,Founded\n"
)
_SP500_CSV = _SP500_CSV_HEADER + "".join(
    f"T{index:03d},Company {index:03d},Information Technology,Semiconductors,"
    f"\"City, ST\",2024-01-01,{1000000 + index},1990\n"
    for index in range(400)
) + (
    "NVDA,NVIDIA Corporation,Information Technology,Semiconductors,"
    "\"Santa Clara, California\",2024-01-01,1045810,1993\n"
)


_NASDAQ_PAGE = """
<html><body>
<table>
<tr><th>No.</th><th>Symbol</th><th>Company Name</th><th>Market Cap</th></tr>
""" + "".join(
    f"<tr><td>{index}</td><td>N{index:03d}</td><td>Nasdaq Co {index:03d}</td><td>1T</td></tr>\n"
    for index in range(1, 81)
) + """
<tr><td>99</td><td>NVDA</td><td>NVIDIA Corporation</td><td>1T</td></tr>
<tr><td>100</td><td>TSLA</td><td>Tesla, Inc.</td><td>1T</td></tr>
<tr><td>101</td><td>AAPL</td><td>Apple Inc.</td><td>1T</td></tr>
</table>
</body></html>
"""


@pytest.mark.asyncio
async def test_live_providers_parse_structured_fixtures():
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("constituents.csv"):
            return httpx.Response(200, text=_SP500_CSV)
        if "nasdaq-100-stocks" in url:
            return httpx.Response(200, text=_NASDAQ_PAGE)
        return httpx.Response(404, text="missing")

    transport = httpx.MockTransport(handler)
    sp_client = WikipediaClient(transport=transport, min_interval_seconds=0)
    ndx_client = WikipediaClient(transport=transport, min_interval_seconds=0)
    try:
        sp500 = await Sp500Provider(client=sp_client, csv_url="https://example.test/constituents.csv").fetch_members()
        nasdaq = await Nasdaq100Provider(
            client=ndx_client, page_url="https://example.test/list/nasdaq-100-stocks/"
        ).fetch_members()
    finally:
        await sp_client.aclose()
        await ndx_client.aclose()

    assert len(sp500) == 401
    assert sp500[-1].ticker == "NVDA"
    assert sp500[-1].sec_cik == "0001045810"
    assert Sp500Provider.provider_name == "datasets_sp500_csv"
    assert len(nasdaq) == 83
    assert {item.ticker for item in nasdaq} >= {"NVDA", "TSLA", "AAPL"}
    assert Nasdaq100Provider.provider_name == "stockanalysis_nasdaq100"


async def test_sp500_and_nasdaq_import_membership_and_removal(session_factory, job_redis):
    async with session_factory() as session:
        session.add(
            Company(
                name="NVIDIA Corporation",
                ticker="NVDA",
                universe_status="WATCHED",
                discovery_source="MANUAL",
                is_active=True,
                market_symbol="NVDA",
                sec_cik="1045810",
            )
        )
        session.add(
            Company(
                name="CoreWeave, Inc.",
                ticker="CRWV",
                universe_status="DISCOVERED",
                discovery_source="SUPPLY_CHAIN",
                is_active=True,
            )
        )
        await session.commit()

        first = await refresh_service.refresh_sp500(session, provider=FixtureSp500(_SP500_HTML))
        second = await refresh_service.refresh_sp500(session, provider=FixtureSp500(_SP500_HTML))
        nasdaq = await refresh_service.refresh_nasdaq100(session, provider=FixtureNasdaq(_NASDAQ_HTML))
        smaller = _SP500_HTML.replace(
            "<tr><td>OLD</td><td>Old Index Corp</td><td>Financials</td><td>Banks</td><td>1111111</td></tr>\n",
            "",
        )
        removed = await refresh_service.refresh_sp500(session, provider=FixtureSp500(smaller))

        nvidia = await session.scalar(select(Company).where(Company.ticker == "NVDA"))
        core = await session.scalar(select(Company).where(Company.ticker == "CRWV"))
        old = await session.scalar(select(Company).where(Company.ticker == "OLD"))
        memberships = list(
            (
                await session.execute(
                    select(UniverseMembership).where(
                        UniverseMembership.company_id == nvidia.id,
                        UniverseMembership.is_active.is_(True),
                    )
                )
            ).scalars()
        )
        old_membership = await session.scalar(
            select(UniverseMembership).where(
                UniverseMembership.company_id == old.id,
                UniverseMembership.universe_name == "SP500",
                UniverseMembership.source == "SP500",
            )
        )
        total = int((await session.execute(select(func.count()).select_from(Company))).scalar_one())

    assert first["created_companies"] == 3
    assert first["existing_companies"] == 1
    assert first["memberships_added"] == 4
    assert second["created_companies"] == 0
    assert second["memberships_existing"] == 4
    assert nasdaq["memberships_added"] == 3
    assert removed["memberships_removed"] == 1
    assert nvidia.universe_status == "WATCHED"
    assert nvidia.discovery_source == "MANUAL"
    assert {item.universe_name for item in memberships} == {"SP500", "NASDAQ100"}
    assert core.universe_status == "DISCOVERED"
    assert old_membership is not None
    assert old_membership.is_active is False
    assert total == 6


async def test_bootstrap_batch_limit_and_job_dedup(session_factory, job_redis, monkeypatch):
    monkeypatch.setattr(settings, "universe_bootstrap_max_per_run", 25)
    async with session_factory() as session:
        for index in range(30):
            session.add(
                Company(
                    name=f"Screened {index:02d}",
                    ticker=f"S{index:02d}",
                    universe_status="SCREENED",
                    discovery_source="SP500",
                    is_active=True,
                    universe_priority=100 - index,
                    market_symbol=f"S{index:02d}",
                    sec_cik=str(2000000 + index).zfill(10),
                )
            )
        await session.flush()
        companies = list((await session.execute(select(Company).where(Company.ticker.like("S%")))).scalars())
        for company in companies:
            session.add(
                UniverseMembership(
                    company_id=company.id,
                    universe_name="SP500",
                    source="SP500",
                    is_active=True,
                    added_at=datetime.now(timezone.utc),
                )
            )
        top = companies[0]
        session.add(
            CompanySyncStatus(
                company_id=top.id,
                source=MARKET_SOURCE,
                last_success_at=datetime.now(timezone.utc),
                consecutive_failures=0,
            )
        )
        await session.commit()
        first = await bootstrap_universe_data(session)
        second = await bootstrap_universe_data(session)
        status = await universe_market_status(session)

    assert first["selected"] == 25
    assert first["market_enqueued"] == 24
    assert first["sec_enqueued"] == 25
    assert second["market_enqueued"] == 0
    assert second["sec_enqueued"] == 0
    assert second["already_active"] >= 24
    assert status["SP500"]["members"] == 30
    assert status["bootstrap"]["pending_market"] == 29
    assert status["bootstrap"]["max_per_run"] == 25


async def test_universe_admin_routes_and_dashboard_defaults(client, session_factory, job_redis, monkeypatch):
    monkeypatch.setattr(
        "app.api.routes.universe.refresh_sp500",
        lambda db: refresh_service.refresh_sp500(db, provider=FixtureSp500(_SP500_HTML.replace(
            "<tr><td>OLD</td><td>Old Index Corp</td><td>Financials</td><td>Banks</td><td>1111111</td></tr>\n",
            "",
        ))),
    )
    monkeypatch.setattr(
        "app.api.routes.universe.refresh_nasdaq100",
        lambda db: refresh_service.refresh_nasdaq100(db, provider=FixtureNasdaq(_NASDAQ_HTML)),
    )

    assert (await client.post("/admin/universe/refresh/sp500")).status_code == 401
    assert (await client.get("/admin/universe/status")).status_code == 401
    headers = await _headers(client, email="universe-index@example.com")

    async with session_factory() as session:
        session.add(
            Company(
                name="NVIDIA Corporation",
                ticker="NVDA",
                universe_status="WATCHED",
                discovery_source="MANUAL",
                is_active=True,
            )
        )
        session.add(
            Company(
                name="CoreWeave, Inc.",
                ticker="CRWV",
                universe_status="DISCOVERED",
                discovery_source="SUPPLY_CHAIN",
                is_active=True,
            )
        )
        await session.commit()

    sp500 = await client.post("/admin/universe/refresh/sp500", headers=headers)
    nasdaq = await client.post("/admin/universe/refresh/nasdaq100", headers=headers)
    bootstrap = await client.post("/admin/universe/bootstrap", headers=headers)
    status = await client.get("/admin/universe/status", headers=headers)
    listed = await client.get("/universe", params={"search": "NVDA", "limit": 10, "offset": 0}, headers=headers)
    page = await client.get("/universe", params={"limit": 1, "offset": 0}, headers=headers)

    await client.post("/auth/register", json={"email": "dash-universe@example.com", "password": "password123"})
    await client.post(
        "/login",
        data={"email": "dash-universe@example.com", "password": "password123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    dashboard = await client.get("/dashboard")
    screened = await client.get("/dashboard", params={"universe_status": "SCREENED"})
    search = await client.get("/dashboard", params={"q": "NVIDIA"})
    admin = await client.get("/admin/view")

    assert sp500.status_code == 200
    assert sp500.json()["universe"] == "SP500"
    assert nasdaq.json()["universe"] == "NASDAQ100"
    assert bootstrap.status_code == 200
    assert bootstrap.json()["limit"] == settings.universe_bootstrap_max_per_run
    assert status.json()["SP500"]["members"] >= 3
    assert listed.json()["total"] >= 1
    assert listed.json()["items"][0]["ticker"] == "NVDA"
    assert page.json()["total"] >= 2
    assert len(page.json()["items"]) == 1
    assert "NVIDIA Corporation" in dashboard.text
    assert "CoreWeave" in dashboard.text
    assert "Apple Inc." not in dashboard.text
    assert "Apple Inc." in screened.text
    assert "NVIDIA Corporation" in search.text
    assert "Univers de marché" in admin.text
    assert "Rafraîchir S&amp;P 500" in admin.text
    assert "Lancer bootstrap" in admin.text


def test_scheduler_enqueues_members_refresh_and_bootstrap(job_redis, monkeypatch):
    first = enqueue_universe_members_refresh()
    second = enqueue_universe_members_refresh()
    boot = enqueue_universe_bootstrap()
    again = enqueue_universe_bootstrap()
    assert first["job_id"] == "universe-members-refresh"
    assert first["enqueued"] is True
    assert second["enqueued"] is False
    assert boot["job_id"] == "universe-bootstrap-data"
    assert again["enqueued"] is False

    calls = {}

    def fake_members():
        calls["members"] = True
        return first

    def fake_boot():
        calls["boot"] = True
        return boot

    monkeypatch.setattr(scheduler, "enqueue_universe_members_refresh", fake_members)
    monkeypatch.setattr(scheduler, "enqueue_universe_bootstrap", fake_boot)
    assert scheduler.run_universe_members_refresh()["job_id"] == "universe-members-refresh"
    assert scheduler.run_universe_bootstrap_scan()["job_id"] == "universe-bootstrap-data"
    assert calls == {"members": True, "boot": True}
    source = inspect.getsource(scheduler)
    assert "refresh_sp500" not in source
    assert "bootstrap_universe_data(" not in source
