"""Discovery expansion stays offline. Collection uses a fixture and jobs use Redis db 15."""

from datetime import datetime, timedelta, timezone
import inspect

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.jobs import scheduler
from app.jobs.queues import (
    discovery_expansion_batch_job_id,
    discovery_expansion_job_id,
    enqueue_discovery_expansion,
    enqueue_discovery_expansion_batch,
    redis_connection,
)
from app.jobs.worker import main as worker_main
from app.models.company import Company
from app.models.intelligence import CompanyExternalSource, ExternalSource
from app.models.supply_chain import CompanyRelationship, DiscoveredCompany
from app.services.discovery_expansion.expansion import expand_discovered_company
from app.services.discovery_expansion.policy import select_due_expansion_ids
from app.services.discovery_expansion.symbols import reliable_market_symbol
from app.services.intelligence.providers.base import FetchBatch, NormalizedDocument
from app.services.jobs.schedule import select_due_company_ids
from app.services.supply_chain.extraction import extract_relationships
from tests.test_quality_score import _headers

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


class FixedProvider:
    provider_name = "sec_submissions"

    def __init__(self) -> None:
        self.calls = 0

    async def fetch_since(self, source, since):
        self.calls += 1
        return FetchBatch(
            [
                NormalizedDocument(
                    external_id="0001769628-26-000001",
                    url="https://www.sec.gov/Archives/edgar/data/1769628/example-8k",
                    title="8-K NVIDIA partners with CoreWeave",
                    published_at=NOW,
                    language="en",
                    author="CoreWeave, Inc.",
                    summary="NVIDIA partners with CoreWeave.",
                    content_text="NVIDIA partners with CoreWeave.",
                    document_type="SEC_FILING",
                    metadata={},
                )
            ]
        )


@pytest.fixture
def job_redis(monkeypatch):
    monkeypatch.setattr(settings, "redis_db", 15)
    connection = redis_connection()
    connection.flushdb()
    yield connection
    connection.flushdb()
    connection.close()


def _record(bucket: list, name: str):
    def enqueue(company_id: int) -> dict:
        bucket.append((name, company_id))
        return {"enqueued": True, "job_id": f"{name}-{company_id}"}

    return enqueue


async def _company(session, ticker: str, **values) -> Company:
    company = Company(name=values.pop("name", f"{ticker} Inc"), ticker=ticker, **values)
    session.add(company)
    await session.commit()
    await session.refresh(company)
    return company


async def test_expansion_registers_one_sec_source_and_market_symbol(session_factory):
    enqueued: list = []
    provider = FixedProvider()
    async with session_factory() as session:
        nvidia = await _company(
            session,
            "NVDA",
            name="NVIDIA",
            universe_status="WATCHED",
            discovery_source="MANUAL",
            discovery_depth=0,
        )
        core = await _company(
            session,
            "CRWV",
            name="CoreWeave, Inc.",
            exchange="Nasdaq",
            sec_cik="1769628",
            country="US",
            universe_status="DISCOVERED",
            discovery_source="SUPPLY_CHAIN",
            is_active=True,
        )
        session.add(
            DiscoveredCompany(
                name="CoreWeave",
                discovered_from_company_id=nvidia.id,
                promoted_company_id=core.id,
                discovery_reason="Promoted from NVIDIA",
                evidence_count=1,
                confidence=90,
                status="IMPORTED",
                document_ids=[],
                verification_status="VERIFIED",
                verification_evidence_json={},
            )
        )
        await session.commit()
        core_id = core.id
        nvidia_id = nvidia.id
        result = await expand_discovered_company(
            session,
            core_id,
            provider=provider,
            enqueue_sec=_record(enqueued, "sec"),
            enqueue_market=_record(enqueued, "market"),
        )
        again = await expand_discovered_company(
            session,
            core_id,
            provider=provider,
            enqueue_sec=_record(enqueued, "sec"),
            enqueue_market=_record(enqueued, "market"),
        )
        stored = await session.get(Company, core_id)
        sources = list((await session.scalars(select(ExternalSource))).all())
        links = list((await session.scalars(select(CompanyExternalSource))).all())
        candidates = list((await session.scalars(select(DiscoveredCompany).where(DiscoveredCompany.name == "CoreWeave"))).all())
        relationships = list((await session.scalars(select(CompanyRelationship))).all())

    assert result["status"] == "analyzed"
    assert stored.universe_status == "DISCOVERED"
    assert stored.discovery_source == "SUPPLY_CHAIN"
    assert stored.discovered_parent_company_id == nvidia_id
    assert stored.discovery_depth == 1
    assert stored.discovery_pipeline_status == "ANALYZED"
    assert stored.market_symbol == "CRWV"
    assert stored.sec_cik == "1769628"
    assert len(sources) == 1
    assert sources[0].provider == "sec_submissions"
    assert sources[0].name == "SEC submissions CRWV"
    assert sources[0].base_url.endswith("CIK0001769628.json")
    assert sources[0].metadata_json["cik"] == "0001769628"
    assert len(links) == 1
    assert links[0].company_id == core_id
    assert links[0].is_primary is True
    assert again["sources"] == 1
    assert len(candidates) == 1
    assert candidates[0].promoted_company_id == core_id
    assert len(relationships) == 1
    assert relationships[0].source_company_id == nvidia_id
    assert relationships[0].target_company_id == core_id
    assert relationships[0].relationship_type == "PARTNER"
    assert ("sec", core_id) in enqueued
    assert ("market", core_id) in enqueued
    assert core_id in await _due_sec(session_factory)


async def _due_sec(session_factory) -> list[int]:
    async with session_factory() as session:
        return await select_due_company_ids(session)


async def test_ambiguous_exchange_does_not_invent_a_symbol(session_factory):
    assert reliable_market_symbol("CRWV", "Nasdaq") == "CRWV"
    assert reliable_market_symbol("2353", "TWSE") is None
    assert reliable_market_symbol("STM", "EPA") is None
    async with session_factory() as session:
        company = await _company(
            session,
            "ACER",
            name="Acer Inc",
            exchange="TWSE",
            universe_status="DISCOVERED",
            sec_cik="12345",
        )
        result = await expand_discovered_company(
            session,
            company.id,
            provider=FixedProvider(),
            enqueue_sec=lambda company_id: {"enqueued": False},
            enqueue_market=lambda company_id: {"enqueued": False},
        )
        stored = await session.get(Company, company.id)
    assert result["market_symbol"] is None
    assert stored.market_symbol is None
    assert stored.universe_status == "DISCOVERED"


async def test_known_company_is_not_imported_again(session_factory):
    async with session_factory() as session:
        nvidia = await _company(session, "NVDA", name="NVIDIA")
        core = await _company(session, "CRWV", name="CoreWeave, Inc.")
        source = ExternalSource(
            name="Fixture",
            source_type="SEC",
            base_url="https://example.com/fixture",
            provider="sec_submissions",
            trust_level="HIGH",
            poll_interval_minutes=720,
            metadata_json={"company_id": nvidia.id},
        )
        session.add(source)
        await session.flush()
        from app.models.intelligence import DocumentCompany, ExternalDocument

        document = ExternalDocument(
            source_id=source.id,
            title="NVIDIA partners with CoreWeave",
            summary="NVIDIA partners with CoreWeave.",
            content_text="NVIDIA partners with CoreWeave.",
            document_type="PRESS_RELEASE",
            fetched_at=NOW,
            published_at=NOW,
            metadata_json={},
        )
        session.add(document)
        await session.flush()
        session.add(
            DocumentCompany(
                document_id=document.id,
                company_id=nvidia.id,
                relation_type="SUBJECT",
                match_method="MANUAL",
                confidence=100,
            )
        )
        await session.commit()
        result = await extract_relationships(session, document.id)
        created = await session.scalar(select(func.count()).select_from(DiscoveredCompany))
        relationship = await session.scalar(select(CompanyRelationship))
    assert result["candidates"] == 0
    assert created == 0
    assert relationship is not None
    assert relationship.target_company_id == core.id


async def test_depth_above_the_limit_is_blocked(session_factory):
    async with session_factory() as session:
        parent = await _company(session, "ROOT", name="Root", discovery_depth=0)
        previous = parent
        for index in range(1, 5):
            previous = await _company(
                session,
                f"D{index}",
                name=f"Depth {index}",
                discovered_parent_company_id=previous.id,
                discovery_depth=index,
                universe_status="DISCOVERED",
                sec_cik=str(1000 + index),
                exchange="Nasdaq",
            )
        calls: list = []
        result = await expand_discovered_company(
            session,
            previous.id,
            provider=FixedProvider(),
            enqueue_sec=_record(calls, "sec"),
            enqueue_market=_record(calls, "market"),
        )
        stored = await session.get(Company, previous.id)
        sources = await session.scalar(select(func.count()).select_from(ExternalSource))
    assert result["status"] == "blocked"
    assert result["detail"] == "max_depth"
    assert stored.discovery_pipeline_status == "BLOCKED"
    assert stored.discovery_depth == 4
    assert stored.universe_status == "DISCOVERED"
    assert calls == []
    assert sources == 0


async def test_due_expansion_is_limited_and_skips_recent_analysis(session_factory, monkeypatch):
    moment = NOW
    async with session_factory() as session:
        ready = await _company(
            session,
            "NEW1",
            name="Ready Co",
            universe_status="DISCOVERED",
            sec_cik="11",
            discovery_depth=1,
            discovery_pipeline_status="READY",
        )
        deeper = await _company(
            session,
            "NEW2",
            name="Deeper Co",
            universe_status="DISCOVERED",
            sec_cik="12",
            discovery_depth=2,
            discovery_pipeline_status="NEW",
        )
        fresh = await _company(
            session,
            "DONE",
            name="Analyzed Co",
            universe_status="DISCOVERED",
            sec_cik="13",
            discovery_pipeline_status="ANALYZED",
            last_discovery_collection_at=moment,
        )
        stale = await _company(
            session,
            "OLD",
            name="Stale Co",
            universe_status="DISCOVERED",
            sec_cik="14",
            discovery_depth=3,
            discovery_pipeline_status="ANALYZED",
            last_discovery_collection_at=moment - timedelta(hours=49),
        )
        await _company(
            session,
            "WATCH",
            name="Watched Co",
            universe_status="WATCHED",
            sec_cik="15",
        )
        await _company(
            session,
            "DEEP",
            name="Too Deep",
            universe_status="DISCOVERED",
            sec_cik="16",
            discovery_depth=4,
            discovery_pipeline_status="READY",
        )
        due = await select_due_expansion_ids(session, now=moment)
        monkeypatch.setattr(settings, "discovery_expansion_max_per_run", 1)
        limited = await select_due_expansion_ids(session, now=moment)
    assert ready.id in due
    assert deeper.id in due
    assert stale.id in due
    assert fresh.id not in due
    assert limited == [ready.id]


async def test_expansion_routes_jobs_scheduler_and_admin(session_factory, client, job_redis, monkeypatch):
    async with session_factory() as session:
        parent = await _company(session, "NVDA", name="NVIDIA", discovery_depth=0)
        company = await _company(
            session,
            "CRWV",
            name="CoreWeave, Inc.",
            universe_status="DISCOVERED",
            discovery_pipeline_status="READY",
            discovery_depth=1,
            discovered_parent_company_id=parent.id,
            sec_cik="1769628",
            market_symbol="CRWV",
            exchange="Nasdaq",
        )
        await _company(session, "COL", name="Collecting", discovery_pipeline_status="COLLECTING")
        await _company(
            session,
            "ANZ",
            name="Analyzed",
            discovery_pipeline_status="ANALYZED",
            discovery_depth=2,
            last_discovery_collection_at=NOW,
        )
        await _company(session, "BLK", name="Blocked", discovery_pipeline_status="BLOCKED")
        company_id = company.id
    for path in (
        f"/companies/{company_id}/discovery-status",
        "/discovery/graph-summary",
        f"/admin/jobs/discovery-expand/{company_id}",
    ):
        denied = await client.get(path) if "jobs" not in path else await client.post(path)
        assert denied.status_code == 401
    headers = await _headers(client)
    status_response = await client.get(f"/companies/{company_id}/discovery-status", headers=headers)
    assert status_response.status_code == 200
    body = status_response.json()
    assert body["discovery_depth"] == 1
    assert body["discovered_parent_company_id"] is not None
    assert body["universe_status"] == "DISCOVERED"
    assert body["sec_sync_eligible"] is True
    assert body["market_symbol"] == "CRWV"
    assert body["max_depth"] == 3
    summary = await client.get("/discovery/graph-summary", headers=headers)
    assert summary.status_code == 200
    assert summary.json()["companies_by_depth"]["1"] == 1
    queued = await client.post(f"/admin/jobs/discovery-expand/{company_id}", headers=headers)
    assert queued.status_code == 202
    assert queued.json()["job_id"] == discovery_expansion_job_id(company_id) == f"expand-discovered-company-{company_id}"
    assert queued.json()["queue"] == "intelligence"
    assert enqueue_discovery_expansion(company_id)["enqueued"] is False
    batch = enqueue_discovery_expansion_batch()
    assert batch["job_id"] == discovery_expansion_batch_job_id() == "expand-discovered-companies"
    assert enqueue_discovery_expansion_batch()["enqueued"] is False
    admin = (await client.get("/admin/status", headers=headers)).json()["discovery_expansion"]
    assert admin["ready"] == 1
    assert admin["collecting"] == 1
    assert admin["analyzed"] == 1
    assert admin["blocked"] == 1
    assert admin["max_depth"] == 3
    assert admin["deepest_company"] == "ANZ"
    assert admin["last_expansion"] is not None

    def fake():
        return {"job_id": "expand-discovered-companies", "queue": "intelligence", "enqueued": True, "status": "queued"}

    monkeypatch.setattr(scheduler, "enqueue_discovery_expansion_batch", fake)
    assert scheduler.run_discovery_expansion_scan()["job_id"] == "expand-discovered-companies"
    source = inspect.getsource(scheduler)
    assert "run_discovery_expansion_scan" in source
    assert "next_expansion" in source
    assert "extract_relationships" not in source
    worker_source = inspect.getsource(worker_main)
    assert worker_source.index("QUEUE_ANALYSIS") < worker_source.index("QUEUE_INTELLIGENCE")
    assert worker_source.index("QUEUE_INTELLIGENCE") < worker_source.index("QUEUE_SEC")


def test_expansion_schedule_defaults():
    assert settings.discovery_max_depth == 3
    assert settings.discovery_expansion_max_per_run == 20
    assert settings.discovery_expansion_scan_hours == 24
    assert settings.discovery_expansion_analyzed_hours == 48
