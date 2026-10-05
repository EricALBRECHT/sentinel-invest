"""Candidate verification stays offline. SEC listings are fixtures and jobs use Redis db 15."""

from datetime import datetime, timezone
import inspect

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.jobs import scheduler
from app.jobs.queues import (
    discovery_verification_batch_job_id,
    enqueue_discovery_verification_batch,
    enqueue_verify_candidate,
    redis_connection,
    verify_candidate_job_id,
)
from app.jobs.worker import main as worker_main
from app.models.company import Company
from app.models.intelligence import CompanyAlias, DocumentCompany, ExternalDocument, ExternalSource
from app.models.supply_chain import CompanyRelationship, DiscoveredCompany, RelationshipEvidence
from app.models.universe_membership import UniverseMembership
from app.services.discovery_verification.promotion import promote_candidate, reject_candidate
from app.services.discovery_verification.resolver import Listing, OfficialIdentity
from app.services.discovery_verification.verifier import select_due_candidate_ids, verification_counts, verify_candidate
from tests.test_quality_score import _headers

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


class FixedDirectory:
    def __init__(self, listings=None, officials=None) -> None:
        self._listings = listings or []
        self._officials = officials or []

    async def listings_for_name(self, name: str):
        return list(self._listings)

    async def enrich(self, listing: Listing):
        return {
            "name": listing.name,
            "tickers": [listing.ticker],
            "exchanges": [listing.exchange] if listing.exchange else [],
            "website": None,
            "country": "US",
        }

    async def official_identities(self, name: str):
        return list(self._officials)


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


async def _candidate(session, company: Company, name: str, **values) -> DiscoveredCompany:
    row = DiscoveredCompany(
        name=name,
        discovered_from_company_id=company.id,
        discovery_reason=values.pop("discovery_reason", f"PARTNER via partners_with with {company.name}"),
        evidence_count=values.pop("evidence_count", 1),
        confidence=values.pop("confidence", 90),
        status=values.pop("status", "CANDIDATE"),
        document_ids=values.pop("document_ids", []),
        verification_evidence_json={},
        **values,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def _document(session, company: Company, text: str, source_name: str) -> ExternalDocument:
    source = ExternalSource(
        name=source_name,
        source_type="COMPANY_IR",
        base_url="https://example.com/releases.xml",
        provider="rss",
        trust_level="HIGH",
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
        document_type="PRESS_RELEASE",
        fetched_at=NOW,
        published_at=NOW,
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


def _listing() -> Listing:
    return Listing(cik="1769628", name="CoreWeave, Inc.", ticker="CRWV", exchange="Nasdaq")


async def test_existing_company_match_does_not_duplicate(session_factory):
    async with session_factory() as session:
        nvidia = await _company(session, "NVDA", name="NVIDIA", discovery_source="MANUAL", universe_status="WATCHED")
        existing = await _company(
            session,
            "CRWV",
            name="CoreWeave",
            discovery_source="MANUAL",
            universe_status="WATCHED",
            universe_priority=25,
        )
        candidate = await _candidate(session, nvidia, "CoreWeave")
        before = await session.scalar(select(func.count()).select_from(Company))
        verified = await verify_candidate(session, candidate.id, directory=FixedDirectory())
        result = await promote_candidate(session, candidate.id)
        assert verified.verification_status == "VERIFIED"
        assert result.promoted is True
        assert result.company_id == existing.id
        assert await session.scalar(select(func.count()).select_from(Company)) == before
        await session.refresh(existing)
        await session.refresh(candidate)
        assert existing.universe_status == "WATCHED"
        assert existing.discovery_source == "MANUAL"
        assert candidate.promoted_company_id == existing.id
        assert candidate.status == "IMPORTED"
        aliases = list(
            (await session.scalars(select(CompanyAlias).where(CompanyAlias.company_id == existing.id))).all()
        )
        assert any(alias.alias == "CRWV" and alias.alias_type == "TICKER" for alias in aliases)


async def test_promotion_creates_discovered_company_and_replays_evidence(session_factory):
    async with session_factory() as session:
        nvidia = await _company(session, "NVDA", name="NVIDIA", discovery_source="MANUAL", universe_status="WATCHED")
        document = await _document(session, nvidia, "NVIDIA partners with CoreWeave", "promote-coreweave")
        candidate = await _candidate(session, nvidia, "CoreWeave", document_ids=[document.id])
        verified = await verify_candidate(session, candidate.id, directory=FixedDirectory([_listing()]))
        assert verified.canonical_name == "CoreWeave, Inc."
        assert verified.ticker == "CRWV"
        assert verified.exchange == "Nasdaq"
        assert verified.country == "US"
        assert verified.sec_cik == "1769628"
        assert verified.entity_type == "PUBLIC_COMPANY"
        assert verified.verification_status == "VERIFIED"
        assert verified.verification_confidence == 100
        assert verified.verification_evidence_json["decision"] == "sec_listing"
        before = await session.scalar(select(func.count()).select_from(Company))
        result = await promote_candidate(session, candidate.id)
        again = await promote_candidate(session, candidate.id)
        assert result.detail == "Promoted"
        assert result.relationships == 1
        assert again.detail == "Already promoted"
        assert again.company_id == result.company_id
        assert await session.scalar(select(func.count()).select_from(Company)) == before + 1
        company = await session.get(Company, result.company_id)
        assert company.universe_status == "DISCOVERED"
        assert company.discovery_source == "SUPPLY_CHAIN"
        assert company.is_active is True
        assert company.ticker == "CRWV"
        assert company.universe_priority == 10
        names = list(
            (
                await session.scalars(
                    select(UniverseMembership.universe_name).where(
                        UniverseMembership.company_id == company.id,
                        UniverseMembership.is_active.is_(True),
                    )
                )
            ).all()
        )
        assert names == ["SUPPLY_CHAIN"]
        aliases = {
            alias.alias
            for alias in (await session.scalars(select(CompanyAlias).where(CompanyAlias.company_id == company.id))).all()
        }
        assert {"CoreWeave", "CRWV"} <= aliases
        await session.refresh(candidate)
        assert candidate.promoted_company_id == company.id
        relationship = await session.scalar(
            select(CompanyRelationship).where(CompanyRelationship.target_company_id == company.id)
        )
        assert relationship.source_company_id == nvidia.id
        assert relationship.relationship_type == "PARTNER"
        assert relationship.direction == "SOURCE_TO_TARGET"
        assert relationship.confidence == 90
        assert relationship.evidence_count == 1
        evidence = await session.scalar(
            select(RelationshipEvidence).where(RelationshipEvidence.relationship_id == relationship.id)
        )
        assert evidence.document_id == document.id


async def test_private_company_keeps_no_ticker(session_factory):
    async with session_factory() as session:
        nvidia = await _company(session, "NVDA", name="NVIDIA", discovery_source="MANUAL")
        candidate = await _candidate(session, nvidia, "OpenAI")
        before = await session.scalar(select(func.count()).select_from(Company))
        official = OfficialIdentity(
            canonical_name="OpenAI",
            entity_type="PRIVATE_COMPANY",
            website="https://openai.com",
            confidence=80,
        )
        verified = await verify_candidate(session, candidate.id, directory=FixedDirectory(officials=[official]))
        assert verified.entity_type == "PRIVATE_COMPANY"
        assert verified.ticker is None
        assert verified.verification_status == "VERIFIED"
        result = await promote_candidate(session, candidate.id)
        assert result.promoted is False
        assert result.detail == "Private company has no ticker"
        assert await session.scalar(select(func.count()).select_from(Company)) == before
        assert verified.promoted_company_id is None


async def test_conflict_stays_partial_and_is_not_promoted(session_factory):
    async with session_factory() as session:
        nvidia = await _company(session, "NVDA", name="NVIDIA", discovery_source="MANUAL")
        candidate = await _candidate(session, nvidia, "CoreWeave")
        listings = [
            _listing(),
            Listing(cik="2", name="CoreWeave Holdings", ticker="CWH", exchange="NYSE"),
        ]
        verified = await verify_candidate(session, candidate.id, directory=FixedDirectory(listings))
        assert verified.verification_status == "PARTIAL"
        assert len(verified.verification_evidence_json["possible_matches"]) == 2
        result = await promote_candidate(session, candidate.id)
        assert result.promoted is False
        assert result.detail == "Candidate is not verified"
        assert await session.scalar(select(func.count()).select_from(Company)) == 1


async def test_rejected_candidate_is_not_promoted(session_factory):
    async with session_factory() as session:
        nvidia = await _company(session, "NVDA", name="NVIDIA", discovery_source="MANUAL")
        candidate = await _candidate(session, nvidia, "Rejected Labs")
        rejected = await reject_candidate(session, candidate.id)
        assert rejected.verification_status == "REJECTED"
        checked = await verify_candidate(session, candidate.id, directory=FixedDirectory([_listing()]))
        assert checked.verification_status == "REJECTED"
        assert checked.ticker is None
        result = await promote_candidate(session, candidate.id)
        assert result.detail == "Candidate was rejected"


async def test_due_candidates_are_limited(session_factory, monkeypatch):
    async with session_factory() as session:
        nvidia = await _company(session, "NVDA", name="NVIDIA")
        ready = await _candidate(session, nvidia, "Ready One", confidence=90)
        weak = await _candidate(session, nvidia, "Too Weak", confidence=50)
        partial = await _candidate(session, nvidia, "Already Partial", confidence=90)
        partial.verification_status = "PARTIAL"
        partial.updated_at = NOW
        await session.commit()
        await _candidate(session, nvidia, "Ready Two", confidence=80)
        due = await select_due_candidate_ids(session, now=NOW)
        assert ready.id in due
        assert weak.id not in due
        assert partial.id not in due
        assert len(due) == 2
        monkeypatch.setattr(settings, "discovery_verify_max_per_run", 1)
        assert await select_due_candidate_ids(session, now=NOW) == [ready.id]


async def test_discovery_routes_jobs_and_admin(client, session_factory, job_redis, monkeypatch):
    assert (await client.get("/discovery/companies/1")).status_code == 401
    assert (await client.post("/discovery/companies/1/verify")).status_code == 401
    assert (await client.post("/discovery/companies/1/promote")).status_code == 401
    assert (await client.post("/discovery/companies/1/reject")).status_code == 401
    assert (await client.post("/admin/jobs/verify-discovered-candidates")).status_code == 401
    monkeypatch.setattr(
        "app.services.discovery_verification.verifier.build_directory",
        lambda: FixedDirectory(),
    )
    async with session_factory() as session:
        nvidia = await _company(session, "NVDA", name="NVIDIA", discovery_source="MANUAL")
        candidate = await _candidate(session, nvidia, "OpenAI")
        verified = await _candidate(session, nvidia, "Verified Name")
        verified.verification_status = "VERIFIED"
        verified.verified_at = NOW
        promoted = await _candidate(session, nvidia, "Promoted Name")
        promoted.verification_status = "VERIFIED"
        promoted.promoted_company_id = nvidia.id
        promoted.status = "IMPORTED"
        rejected = await _candidate(session, nvidia, "Rejected Name")
        rejected.verification_status = "REJECTED"
        rejected.status = "REJECTED"
        await session.commit()
        candidate_id = candidate.id
    headers = await _headers(client)
    verified_response = await client.post(f"/discovery/companies/{candidate_id}/verify", headers=headers)
    assert verified_response.status_code == 200
    assert verified_response.json()["verification_status"] == "PARTIAL"
    listed = await client.get("/discovery/companies", headers=headers, params={"verification_status": "PARTIAL"})
    assert any(item["name"] == "OpenAI" for item in listed.json())
    assert (await client.post(f"/discovery/companies/{candidate_id}/promote", headers=headers)).status_code == 409
    body = (await client.get("/admin/status", headers=headers)).json()["discovery_verification"]
    assert body["unverified"] == 0
    assert body["partial"] == 1
    assert body["verified"] == 2
    assert body["promoted"] == 1
    assert body["rejected"] == 1
    assert body["last_verification"] is not None
    first = enqueue_verify_candidate(8)
    assert first["job_id"] == verify_candidate_job_id(8) == "verify-candidate-8"
    assert first["queue"] == "intelligence"
    assert first["enqueued"] is True
    assert enqueue_verify_candidate(8)["enqueued"] is False
    batch = enqueue_discovery_verification_batch()
    assert batch["job_id"] == discovery_verification_batch_job_id() == "verify-discovered-candidates"
    assert batch["enqueued"] is True
    assert enqueue_discovery_verification_batch()["enqueued"] is False

    def fake():
        return {"job_id": "verify-discovered-candidates", "queue": "intelligence", "enqueued": True, "status": "queued"}

    monkeypatch.setattr(scheduler, "enqueue_discovery_verification_batch", fake)
    assert scheduler.run_discovery_verification_scan()["job_id"] == "verify-discovered-candidates"
    source = inspect.getsource(scheduler)
    assert "run_discovery_verification_scan" in source
    assert "next_discovery" in source
    assert "company_tickers" not in source
    assert "extract_relationships" not in source
    worker_source = inspect.getsource(worker_main)
    assert worker_source.index("QUEUE_ANALYSIS") < worker_source.index("QUEUE_INTELLIGENCE")
    assert worker_source.index("QUEUE_INTELLIGENCE") < worker_source.index("QUEUE_SEC")


def test_verification_schedule_defaults():
    assert settings.discovery_verify_scan_hours == 24
    assert settings.discovery_verify_max_per_run == 20
    assert settings.discovery_verify_min_confidence == 75
