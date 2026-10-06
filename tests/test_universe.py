"""Universe membership stays inside the test database. The priority job uses Redis db 15."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import inspect

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.jobs import scheduler
from app.jobs.queues import enqueue_universe_priorities, redis_connection
from app.models.company import Company
from app.models.company_score import CompanyScore
from app.models.opportunity_score import OpportunityScore
from app.models.universe_membership import UniverseMembership
from app.services.universe.manager import (
    add_company_to_universe,
    archive_stale_companies,
    recalculate_universe_priorities,
    remove_company_from_universe,
)
from app.services.universe.ranking import PriorityInputs, priority_parts, score_priority
from tests.test_quality_score import _headers


@pytest.fixture
def job_redis(monkeypatch):
    monkeypatch.setattr(settings, "redis_db", 15)
    connection = redis_connection()
    connection.flushdb()
    yield connection
    connection.flushdb()
    connection.close()


async def _company(session, ticker: str, **values) -> Company:
    company = Company(name=values.pop("name", f"{ticker} Inc"), ticker=ticker, **values)
    session.add(company)
    await session.commit()
    await session.refresh(company)
    return company


def test_priority_parts_and_cap():
    capped = score_priority(30 + 20 + 15 + 15 + 10 + 10 + 5)
    assert capped == 100
    parts = priority_parts(
        PriorityInputs(
            universe_status="PORTFOLIO",
            quality_score=Decimal("98.79"),
            opportunity_score=Decimal("80"),
            ranking_eligible=True,
            active_universe_count=2,
            discovery_source="SUPPLY_CHAIN",
            pea_eligible=True,
        )
    )
    assert parts["portfolio"] == 30
    assert parts["deep_analysis"] == 0
    assert parts["quality"] == 15
    assert parts["opportunity"] == 15
    assert parts["multi_universe"] == 10
    assert parts["discovery"] == 10
    assert parts["pea"] == 5
    assert parts["total"] == 85
    assert parts["total"] <= 100


async def test_membership_add_remove_and_archive(session_factory):
    async with session_factory() as session:
        company = await _company(session, "MULTI")
        first, created = await add_company_to_universe(session, company.id, "sp500", "manual")
        second, created_again = await add_company_to_universe(session, company.id, "NASDAQ100", "MANUAL")
        duplicate, created_duplicate = await add_company_to_universe(session, company.id, "SP500", "MANUAL")
        await session.refresh(company)
        removed = await remove_company_from_universe(session, company.id, "SP500", "MANUAL")
        active = (
            await session.execute(
                select(func.count())
                .select_from(UniverseMembership)
                .where(UniverseMembership.company_id == company.id, UniverseMembership.is_active.is_(True))
            )
        ).scalar_one()
        company.last_seen_at = datetime.now(timezone.utc) - timedelta(days=400)
        await session.commit()
        archived = await archive_stale_companies(session, stale_days=365)
        await session.refresh(company)

    assert created is True
    assert created_again is True
    assert created_duplicate is False
    assert duplicate.id == first.id
    assert second.universe_name == "NASDAQ100"
    assert company.universe_status == "ARCHIVED"
    assert company.is_active is False
    assert company.universe_priority == 0
    assert removed.is_active is False
    assert removed.removed_at is not None
    assert active == 1
    assert archived == [company.id]


async def test_recalculate_priority_uses_scores_and_memberships(session_factory):
    async with session_factory() as session:
        company = await _company(
            session,
            "PRIO",
            universe_status="PORTFOLIO",
            discovery_source="BOTTLENECK",
            pea_eligible=True,
        )
        await add_company_to_universe(session, company.id, "SP500", "MANUAL", universe_status="PORTFOLIO")
        await add_company_to_universe(session, company.id, "PEA", "PEA", universe_status="PORTFOLIO")
        session.add(
            CompanyScore(
                company_id=company.id,
                score_date=date(2026, 10, 5),
                quality_score=Decimal("80"),
                overall_confidence_score=70,
                metrics_json={},
                confidence_json={},
                method_version="quality_v1",
            )
        )
        session.add(
            OpportunityScore(
                company_id=company.id,
                score_date=date(2026, 10, 5),
                opportunity_score=Decimal("75"),
                opportunity_confidence_score=40,
                coverage_score=80,
                coverage_status="USABLE",
                ranking_eligible=True,
                components_json={},
                confidence_json={},
                method_version="opportunity_v1",
            )
        )
        await session.commit()
        result = await recalculate_universe_priorities(session)
        await session.refresh(company)

    assert result["changed"] == 1
    assert company.universe_priority == 85


async def test_universe_routes_filters_import_and_admin(client):
    denied = await client.get("/universe")
    denied_named = await client.get("/universe/SP500")
    denied_add = await client.post("/universe/companies/1", json={"universe_name": "SP500", "source": "MANUAL"})
    denied_delete = await client.delete("/universe/companies/1", params={"universe_name": "SP500", "source": "MANUAL"})
    denied_list = await client.get("/companies/1/universes")
    denied_priority = await client.post("/companies/1/universe/recalculate-priority")
    denied_import = await client.post("/admin/universe/import", json={"rows": []})
    assert denied.status_code == 401
    assert denied_named.status_code == 401
    assert denied_add.status_code == 401
    assert denied_delete.status_code == 401
    assert denied_list.status_code == 401
    assert denied_priority.status_code == 401
    assert denied_import.status_code == 401

    headers = await _headers(client, email="universe@example.com")
    imported = await client.post(
        "/admin/universe/import",
        json={
            "csv": "ticker,name,country,exchange,universe_name,source\n"
            "ACME,Acme Corp,US,NYSE,SP500,SP500\n"
            "ACME,Acme Corp,US,NYSE,NASDAQ100,NASDAQ100\n"
            "BETA,Beta SA,FR,EPA,PEA,PEA\n"
        },
        headers=headers,
    )
    again = await client.post(
        "/admin/universe/import",
        json={
            "rows": [
                {
                    "ticker": "ACME",
                    "name": "Acme Corp",
                    "country": "US",
                    "exchange": "NYSE",
                    "universe_name": "SP500",
                    "source": "SP500",
                }
            ]
        },
        headers=headers,
    )
    listed = await client.get("/universe", headers=headers)
    sp500 = await client.get("/universe/SP500", headers=headers)
    pea = await client.get("/universe", params={"source": "PEA", "status": "WATCHED"}, headers=headers)
    page = await client.get("/universe", params={"limit": 1, "offset": 0}, headers=headers)
    acme = next(item for item in listed.json()["items"] if item["ticker"] == "ACME")
    universes = await client.get(f"/companies/{acme['id']}/universes", headers=headers)
    removed = await client.delete(
        f"/universe/companies/{acme['id']}",
        params={"universe_name": "NASDAQ100", "source": "NASDAQ100"},
        headers=headers,
    )
    priority = await client.post(f"/companies/{acme['id']}/universe/recalculate-priority", headers=headers)
    admin = await client.get("/admin/status", headers=headers)

    assert imported.status_code == 200
    assert imported.json()["created_companies"] == 2
    assert imported.json()["memberships_added"] == 3
    assert again.json()["memberships_existing"] == 1
    assert again.json()["created_companies"] == 0
    assert {item["ticker"] for item in listed.json()["items"]} == {"ACME", "BETA"}
    assert [item["ticker"] for item in sp500.json()["items"]] == ["ACME"]
    assert [item["ticker"] for item in pea.json()["items"]] == ["BETA"]
    assert len(page.json()["items"]) == 1
    assert page.json()["total"] == 2
    assert {item["universe_name"] for item in universes.json()} == {"SP500", "NASDAQ100"}
    assert removed.status_code == 200
    assert removed.json()["is_active"] is False
    assert priority.status_code == 200
    assert priority.json()["universe_priority"] == 0
    body = admin.json()["universe"]
    assert body["watched"] == 2
    assert body["active_companies"] == 2
    assert body["universes"]["SP500"] == 1
    assert body["universes"]["NASDAQ100"] == 0
    assert body["universes"]["PEA"] == 1
    assert body["archived"] == 0


def test_daily_priority_job_is_enqueued_once(job_redis, monkeypatch):
    first = enqueue_universe_priorities()
    second = enqueue_universe_priorities()
    assert first["job_id"] == "universe-priorities"
    assert first["queue"] == "default"
    assert first["enqueued"] is True
    assert second["enqueued"] is False

    calls = {}

    def fake():
        calls["enqueued"] = True
        return first

    monkeypatch.setattr(scheduler, "enqueue_universe_priorities", fake)
    assert scheduler.run_priority_scan()["job_id"] == "universe-priorities"
    assert calls["enqueued"] is True
    source = inspect.getsource(scheduler)
    assert "recalculate_universe_priorities" not in source
    assert "sync_sec_financials" not in source
