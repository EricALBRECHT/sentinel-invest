"""Supply-chain rules stay offline. Jobs use Redis db 15."""

from datetime import datetime, timedelta, timezone
import inspect

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.jobs import scheduler
from app.jobs.queues import enqueue_supply_chain, redis_connection, supply_chain_job_id
from app.jobs.worker import main as worker_main
from app.models.company import Company
from app.models.company_sync_status import CompanySyncStatus
from app.models.intelligence import DocumentCompany, ExternalDocument, ExternalSource
from app.models.supply_chain import CompanyRelationship, DiscoveredCompany, RelationshipEvidence
from app.services.supply_chain.extraction import (
    _interval_hours,
    extract_relationships,
    process_company_documents,
    select_due_company_ids,
)
from app.services.supply_chain.graph import get_company_graph
from app.services.supply_chain.relationships import SUPPLY_CHAIN_SOURCE, upsert_relationship
from app.services.supply_chain.rules import Entity, automatic_status, find_relationships
from tests.test_quality_score import _headers

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


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


async def _document(session, company: Company, text: str, **values) -> ExternalDocument:
    source = ExternalSource(
        name=values.pop("source_name"),
        source_type="COMPANY_IR",
        base_url="https://example.com/releases.xml",
        provider="rss",
        trust_level=values.pop("trust_level", "HIGH"),
        poll_interval_minutes=120,
        metadata_json={"company_id": company.id},
    )
    session.add(source)
    await session.flush()
    document = ExternalDocument(
        source_id=source.id,
        title=text[:180],
        summary=text,
        content_text=text,
        document_type=values.pop("document_type", "PRESS_RELEASE"),
        fetched_at=NOW,
        published_at=values.pop("published_at", NOW),
        metadata_json={"retention": "text_only_v1"},
    )
    session.add(document)
    await session.flush()
    session.add(
        DocumentCompany(
            document_id=document.id,
            company_id=company.id,
            relation_type="SUBJECT",
            match_method="MANUAL",
            confidence=100,
        )
    )
    await session.commit()
    await session.refresh(document)
    return document


async def _touching(session, company_id: int) -> list[CompanyRelationship]:
    return list(
        (
            await session.scalars(
                select(CompanyRelationship).where(
                    (CompanyRelationship.source_company_id == company_id)
                    | (CompanyRelationship.target_company_id == company_id)
                )
            )
        ).all()
    )


def test_confirmation_thresholds():
    assert automatic_status(100, 1) == "CONFIRMED"
    assert automatic_status(90, 1) == "CONFIRMED"
    assert automatic_status(75, 1) == "DISCOVERED"
    assert automatic_status(75, 2) == "CONFIRMED"
    assert automatic_status(50, 3) == "DISCOVERED"


def test_a_bare_and_is_not_a_relationship():
    nvidia = Entity("NVIDIA", ("NVIDIA",), 1, "NVDA")
    coreweave = Entity("CoreWeave", ("CoreWeave",), None, None)
    hits = find_relationships("NVIDIA and CoreWeave Close the Loop on Agentic AI", [nvidia, coreweave])
    assert hits == []


async def test_supplier_direction_and_importance(session_factory):
    async with session_factory() as session:
        nvidia = await _company(session, "NVDA", name="NVIDIA", discovery_source="MANUAL")
        tsmc = await _company(session, "TSM", name="TSMC")
        document = await _document(
            session,
            nvidia,
            "TSMC manufactures chips for NVIDIA and is the sole supplier",
            source_name="nvda-supplier",
        )
        result = await extract_relationships(session, document.id)
        assert result["relationships"] == 1
        row = (await _touching(session, nvidia.id))[0]
        assert row.source_company_id == tsmc.id
        assert row.target_company_id == nvidia.id
        assert row.relationship_type == "SUPPLIER"
        assert row.direction == "SOURCE_TO_TARGET"
        assert row.confidence == 100
        assert row.status == "CONFIRMED"
        assert row.importance == "CRITICAL"
        assert row.evidence_count == 1


async def test_customer_relationship(session_factory):
    async with session_factory() as session:
        nvidia = await _company(session, "NVDA", name="NVIDIA", discovery_source="MANUAL")
        broadcom = await _company(session, "AVGO", name="Broadcom")
        document = await _document(
            session,
            nvidia,
            "Broadcom selected NVIDIA platform for its next accelerator",
            source_name="nvda-customer",
        )
        await extract_relationships(session, document.id)
        row = (await _touching(session, nvidia.id))[0]
        assert row.source_company_id == broadcom.id
        assert row.target_company_id == nvidia.id
        assert row.relationship_type == "CUSTOMER"
        assert row.confidence == 90
        assert row.status == "CONFIRMED"


async def test_partner_relationship_direction(session_factory):
    async with session_factory() as session:
        nvidia = await _company(session, "NVDA", name="NVIDIA", discovery_source="MANUAL")
        coreweave = await _company(session, "CRWV", name="CoreWeave")
        document = await _document(
            session,
            nvidia,
            "NVIDIA partners with CoreWeave on agentic AI",
            source_name="nvda-partner",
        )
        await extract_relationships(session, document.id)
        row = (await _touching(session, nvidia.id))[0]
        assert row.source_company_id == nvidia.id
        assert row.target_company_id == coreweave.id
        assert row.relationship_type == "PARTNER"
        assert row.direction == "SOURCE_TO_TARGET"
        assert await session.scalar(select(func.count()).select_from(DiscoveredCompany)) == 0


async def test_no_self_relationship(session_factory):
    async with session_factory() as session:
        nvidia = await _company(session, "NVDA", name="NVIDIA")
        document = await _document(session, nvidia, "NVIDIA partners with NVIDIA", source_name="nvda-self")
        result = await extract_relationships(session, document.id)
        assert result["relationships"] == 0
        assert await session.scalar(select(func.count()).select_from(CompanyRelationship)) == 0
        session.add(
            CompanyRelationship(
                source_company_id=nvidia.id,
                target_company_id=nvidia.id,
                relationship_type="PARTNER",
                direction="SOURCE_TO_TARGET",
                confidence=90,
                importance="LOW",
                first_seen_at=NOW,
                last_seen_at=NOW,
                evidence_count=1,
                status="DISCOVERED",
                discovery_method="RULE",
                updated_at=NOW,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


async def test_multiple_evidence_confidence_and_priority(session_factory):
    async with session_factory() as session:
        nvidia = await _company(session, "NVDA", name="NVIDIA", discovery_source="MANUAL")
        other = await _company(session, "ACME", name="Acme Compute", universe_status="DISCOVERED", universe_priority=0)
        weak = await upsert_relationship(
            session,
            source_company_id=other.id,
            target_company_id=nvidia.id,
            relationship_type="CUSTOMER",
            confidence=50,
            importance="LOW",
            evidence_text="weak inference",
            seen_at=NOW,
        )
        await upsert_relationship(
            session,
            source_company_id=other.id,
            target_company_id=nvidia.id,
            relationship_type="CUSTOMER",
            confidence=50,
            importance="LOW",
            evidence_text="another weak note",
            seen_at=NOW,
        )
        await session.commit()
        assert weak.evidence_count == 2
        assert weak.status == "DISCOVERED"
        first = await _document(session, nvidia, "placeholder one", source_name="indirect-1")
        indirect = await upsert_relationship(
            session,
            source_company_id=other.id,
            target_company_id=nvidia.id,
            relationship_type="SUPPLIER",
            confidence=75,
            importance="MEDIUM",
            evidence_text="indirect wording",
            document_id=first.id,
            seen_at=NOW,
        )
        repeated = await upsert_relationship(
            session,
            source_company_id=other.id,
            target_company_id=nvidia.id,
            relationship_type="SUPPLIER",
            confidence=75,
            importance="MEDIUM",
            evidence_text="same document",
            document_id=first.id,
            seen_at=NOW,
        )
        await session.commit()
        assert indirect.status == "DISCOVERED"
        assert repeated.evidence_count == 1
        assert repeated.id == indirect.id
        second = await _document(session, nvidia, "placeholder two", source_name="indirect-2")
        confirmed = await upsert_relationship(
            session,
            source_company_id=other.id,
            target_company_id=nvidia.id,
            relationship_type="SUPPLIER",
            confidence=75,
            importance="MEDIUM",
            evidence_text="second indirect document",
            document_id=second.id,
            seen_at=NOW,
        )
        await session.commit()
        assert confirmed.evidence_count == 2
        assert confirmed.status == "CONFIRMED"
        assert confirmed.confidence == 75
        evidences = (
            await session.scalars(
                select(RelationshipEvidence).where(RelationshipEvidence.relationship_id == confirmed.id)
            )
        ).all()
        assert len(evidences) == 2
        await session.refresh(other)
        assert other.universe_status == "DISCOVERED"
        assert other.discovery_source == "SUPPLY_CHAIN"
        assert other.universe_priority == 10
        await session.refresh(nvidia)
        assert nvidia.discovery_source == "MANUAL"


async def test_supplier_documents_deduplicate(session_factory):
    async with session_factory() as session:
        nvidia = await _company(session, "NVDA", name="NVIDIA", discovery_source="MANUAL")
        tsmc = await _company(session, "TSM", name="TSMC", discovery_source="MANUAL")
        first = await _document(session, nvidia, "TSMC manufactures chips for NVIDIA", source_name="dedup-1")
        await extract_relationships(session, first.id)
        await extract_relationships(session, first.id)
        row = (await _touching(session, nvidia.id))[0]
        assert row.evidence_count == 1
        second = await _document(session, nvidia, "NVIDIA uses TSMC for leading wafers", source_name="dedup-2")
        await extract_relationships(session, second.id)
        rows = await _touching(session, nvidia.id)
        assert len(rows) == 1
        assert rows[0].source_company_id == tsmc.id
        assert rows[0].target_company_id == nvidia.id
        assert rows[0].evidence_count == 2
        assert rows[0].relationship_type == "SUPPLIER"


async def test_unknown_company_stays_a_candidate(session_factory):
    async with session_factory() as session:
        nvidia = await _company(
            session,
            "NVDA",
            name="NVIDIA",
            discovery_source="MANUAL",
            universe_status="WATCHED",
        )
        before = await session.scalar(select(func.count()).select_from(Company))
        document = await _document(
            session,
            nvidia,
            "Building on nearly a decade of co-engineering, CoreWeave has built NVIDIA compute into a cloud",
            source_name="coreweave-candidate",
        )
        result = await extract_relationships(session, document.id)
        assert result["relationships"] == 0
        assert await session.scalar(select(func.count()).select_from(Company)) == before
        candidate = await session.scalar(select(DiscoveredCompany))
        assert candidate.name == "CoreWeave"
        assert candidate.status == "CANDIDATE"
        assert candidate.discovered_from_company_id == nvidia.id
        assert candidate.confidence == 90
        assert candidate.evidence_count == 1
        assert "PARTNER" in candidate.discovery_reason
        await session.refresh(nvidia)
        assert nvidia.universe_status == "WATCHED"


async def test_known_company_is_not_rediscovered(session_factory):
    async with session_factory() as session:
        nvidia = await _company(session, "NVDA", name="NVIDIA", discovery_source="MANUAL")
        await _company(session, "CRWV", name="CoreWeave", discovery_source="MANUAL")
        document = await _document(session, nvidia, "NVIDIA partners with CoreWeave", source_name="known-coreweave")
        await extract_relationships(session, document.id)
        assert await session.scalar(select(func.count()).select_from(DiscoveredCompany)) == 0
        assert await session.scalar(select(func.count()).select_from(CompanyRelationship)) == 1


async def test_equipment_provider_direction(session_factory):
    async with session_factory() as session:
        tsmc = await _company(session, "TSM", name="TSMC", discovery_source="MANUAL")
        asml = await _company(session, "ASML", name="ASML", discovery_source="MANUAL")
        document = await _document(
            session,
            tsmc,
            "ASML supplies lithography systems to TSMC",
            source_name="asml-equipment",
        )
        await extract_relationships(session, document.id)
        rows = await _touching(session, tsmc.id)
        assert len(rows) == 1
        assert rows[0].source_company_id == asml.id
        assert rows[0].target_company_id == tsmc.id
        assert rows[0].relationship_type == "EQUIPMENT_PROVIDER"
        assert rows[0].confidence == 100


async def test_graph_depth_stops_at_two(session_factory):
    async with session_factory() as session:
        nvidia = await _company(session, "NVDA", name="NVIDIA", discovery_source="MANUAL")
        tsmc = await _company(session, "TSM", name="TSMC", discovery_source="MANUAL")
        asml = await _company(session, "ASML", name="ASML", discovery_source="MANUAL")
        further = await _company(session, "ZEISS", name="Zeiss", discovery_source="MANUAL")
        await upsert_relationship(
            session,
            source_company_id=tsmc.id,
            target_company_id=nvidia.id,
            relationship_type="SUPPLIER",
            confidence=100,
            importance="HIGH",
            evidence_text="TSMC manufactures chips for NVIDIA",
            seen_at=NOW,
        )
        await upsert_relationship(
            session,
            source_company_id=asml.id,
            target_company_id=tsmc.id,
            relationship_type="EQUIPMENT_PROVIDER",
            confidence=100,
            importance="HIGH",
            evidence_text="ASML supplies lithography systems to TSMC",
            seen_at=NOW,
        )
        await upsert_relationship(
            session,
            source_company_id=further.id,
            target_company_id=asml.id,
            relationship_type="SUPPLIER",
            confidence=90,
            importance="MEDIUM",
            evidence_text="Zeiss supplies optics to ASML",
            seen_at=NOW,
        )
        await session.commit()
        near = await get_company_graph(session, nvidia.id, depth=1)
        assert near["depth"] == 1
        assert {node["ticker"] for node in near["nodes"]} == {"NVDA", "TSM"}
        assert len(near["edges"]) == 1
        far = await get_company_graph(session, nvidia.id, depth=2)
        assert far["depth"] == 2
        assert {node["ticker"] for node in far["nodes"]} == {"NVDA", "TSM", "ASML"}
        assert len(far["edges"]) == 2
        assert all(edge["direction"] == "SOURCE_TO_TARGET" for edge in far["edges"])


async def test_supply_chain_routes_and_admin_status(client, session_factory):
    assert (await client.get("/companies/1/relationships")).status_code == 401
    assert (await client.get("/companies/1/supply-chain")).status_code == 401
    assert (await client.get("/discovery/companies")).status_code == 401
    assert (await client.post("/admin/jobs/supply-chain-process/1")).status_code == 401
    async with session_factory() as session:
        nvidia = await _company(
            session,
            "NVDA",
            name="NVIDIA",
            discovery_source="MANUAL",
            universe_status="WATCHED",
        )
        tsmc = await _company(session, "TSM", name="TSMC", discovery_source="MANUAL")
        document = await _document(session, nvidia, "TSMC manufactures chips for NVIDIA", source_name="route-supplier")
        await extract_relationships(session, document.id)
        vague = await _document(
            session,
            nvidia,
            "GPT-6 Astra Ultrafast, running on NVIDIA Blackwell GPUs, is available now in the OpenAI API",
            source_name="route-openai",
        )
        await extract_relationships(session, vague.id)
        nvidia_id = nvidia.id
        tsmc_id = tsmc.id
    headers = await _headers(client)
    listed = await client.get(
        f"/companies/{nvidia_id}/relationships",
        headers=headers,
        params={"type": "SUPPLIER", "direction": "inbound", "confidence_min": 90},
    )
    assert listed.status_code == 200
    body = listed.json()
    assert len(body) == 1
    assert body[0]["source_company_id"] == tsmc_id
    assert body[0]["relationship_type"] == "SUPPLIER"
    graph = await client.get(f"/companies/{nvidia_id}/supply-chain", headers=headers, params={"depth": 1})
    assert graph.status_code == 200
    assert graph.json()["depth"] == 1
    assert any(item["name"] == "OpenAI" for item in graph.json()["candidates"])
    found = await client.get(
        "/discovery/companies",
        headers=headers,
        params={"status": "CANDIDATE", "confidence_min": 70, "source_company_id": nvidia_id},
    )
    assert found.status_code == 200
    assert [item["name"] for item in found.json()] == ["OpenAI"]
    status = await client.get("/admin/status", headers=headers)
    assert status.status_code == 200
    supply = status.json()["supply_chain"]
    assert supply["relationships_total"] == 1
    assert supply["confirmed_relationships"] == 1
    assert supply["candidate_relationships"] == 0
    assert supply["discovered_companies"] == 1
    assert supply["last_processing"] is not None


def test_supply_chain_job_and_schedule(job_redis, monkeypatch):
    first = enqueue_supply_chain(2)
    second = enqueue_supply_chain(2)
    assert first["job_id"] == supply_chain_job_id(2) == "supply-chain-company-2"
    assert first["queue"] == "intelligence"
    assert first["enqueued"] is True
    assert second["enqueued"] is False
    assert _interval_hours("PORTFOLIO") == 24
    assert _interval_hours("DEEP_ANALYSIS") == 24
    assert _interval_hours("WATCHED") == 48
    assert _interval_hours("DISCOVERED") is None
    assert _interval_hours("SCREENED") is None

    def fake():
        return {"selected": 0, "enqueued": 0, "already_active": 0, "job_ids": []}

    monkeypatch.setattr(scheduler, "enqueue_due_supply_chain", fake)
    assert scheduler.run_supply_chain_scan()["enqueued"] == 0
    source = inspect.getsource(scheduler)
    assert "run_supply_chain_scan" in source
    assert "extract_relationships" not in source
    assert "fetch_since" not in source
    worker_source = inspect.getsource(worker_main)
    assert worker_source.index("QUEUE_ANALYSIS") < worker_source.index("QUEUE_INTELLIGENCE")
    assert worker_source.index("QUEUE_INTELLIGENCE") < worker_source.index("QUEUE_SEC")


async def test_due_companies_follow_universe_status(session_factory):
    async with session_factory() as session:
        watched = await _company(session, "NVDA", name="NVIDIA", universe_status="WATCHED", universe_priority=25)
        discovered = await _company(session, "NEW", name="Newco", universe_status="DISCOVERED")
        await _document(session, watched, "NVIDIA announced a product", source_name="due-watched")
        await _document(session, discovered, "Newco announced a product", source_name="due-discovered")
        due = await select_due_company_ids(session, now=NOW)
        assert due == [watched.id]
        result = await process_company_documents(session, watched.id)
        assert result["status"] == "success"
        assert result["documents"] == 1
        sync = await session.scalar(
            select(CompanySyncStatus).where(
                CompanySyncStatus.company_id == watched.id,
                CompanySyncStatus.source == SUPPLY_CHAIN_SOURCE,
            )
        )
        sync.last_success_at = NOW
        await session.commit()
        later = await select_due_company_ids(session, now=NOW + timedelta(hours=24))
        assert watched.id not in later
        after = await select_due_company_ids(session, now=NOW + timedelta(hours=49))
        assert watched.id in after
