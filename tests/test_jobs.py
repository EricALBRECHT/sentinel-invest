"""Jobs stay offline. SEC is a fake client, and queue tests use Redis database 15."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import inspect

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.jobs import scheduler
from app.jobs.maintenance import enqueue_selected
from app.jobs.queues import redis_connection, score_retry, sec_job_id, sec_retry
from app.jobs.worker import main as worker_main
from app.models.company import Company
from app.models.company_sync_status import CompanySyncStatus
from app.models.financial_metric import FinancialMetric
from app.models.opportunity_profile import OpportunityProfile
from app.services.jobs.errors import brief_error
from app.services.jobs.scoring import execute_opportunity, execute_quality
from app.services.jobs.sec_sync import execute_sec_sync
from app.services.sec.errors import SecClientError
from app.services.sec.mappings import SEC_SOURCE
from tests.sec_fixtures import duration, facts_payload
from tests.test_quality_score import _headers


@pytest.fixture
def job_redis(monkeypatch):
    monkeypatch.setattr(settings, "redis_db", 15)
    connection = redis_connection()
    connection.flushdb()
    yield connection
    connection.flushdb()
    connection.close()


def _payload() -> dict:
    return facts_payload(
        (
            "us-gaap",
            "Revenues",
            "USD",
            [duration("2023-01-30", "2024-01-28", 60, fy=2024, fp="FY")],
        ),
    )


class FakeSec:
    def __init__(self, payload=None, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error

    async def get_company_facts(self, cik: str) -> dict:
        if self.error is not None:
            raise self.error
        return self.payload

    async def aclose(self) -> None:
        return None


def _record(calls: list[int]):
    def enqueue(company_id: int) -> dict:
        calls.append(company_id)
        return {"enqueued": True, "status": "queued"}

    return enqueue


async def _company(session, ticker: str, *, cik: str | None) -> Company:
    company = Company(name=f"{ticker} Inc", ticker=ticker, sec_cik=cik)
    session.add(company)
    await session.commit()
    await session.refresh(company)
    return company


async def test_sync_success_updates_status_and_enqueues_scores(session_factory):
    quality: list[int] = []
    opportunity: list[int] = []
    async with session_factory() as session:
        company = await _company(session, "NVDS", cik="1045810")
        session.add(OpportunityProfile(company_id=company.id, method_version="opportunity_v1"))
        await session.commit()
        result = await execute_sec_sync(
            session,
            company.id,
            client=FakeSec(_payload()),
            enqueue_quality_job=_record(quality),
            enqueue_opportunity_job=_record(opportunity),
        )
        status = (
            await session.execute(
                select(CompanySyncStatus).where(CompanySyncStatus.company_id == company.id)
            )
        ).scalar_one()
        quality_result = await execute_quality(session, company.id)

    assert result["sync"] == "success"
    assert result["ticker"] == "NVDS"
    assert result["periods_found"] == 1
    assert result["created"] == 1
    assert result["quality_job_enqueued"] is True
    assert result["opportunity_job_enqueued"] is True
    assert quality == [company.id]
    assert opportunity == [company.id]
    assert status.source == SEC_SOURCE
    assert status.last_success_at is not None
    assert status.last_attempt_at is not None
    assert status.consecutive_failures == 0
    assert status.last_error_message is None
    assert status.last_result_json["created"] == 1
    assert quality_result["status"] == "success"


async def test_sync_without_profile_skips_opportunity(session_factory):
    quality: list[int] = []
    opportunity: list[int] = []
    async with session_factory() as session:
        company = await _company(session, "NOPR", cik="0000320193")
        result = await execute_sec_sync(
            session,
            company.id,
            client=FakeSec(_payload()),
            enqueue_quality_job=_record(quality),
            enqueue_opportunity_job=_record(opportunity),
        )
        skipped = await execute_opportunity(session, company.id)

    assert result["quality_job_enqueued"] is True
    assert result["opportunity_job_enqueued"] is False
    assert quality == [company.id]
    assert opportunity == []
    assert skipped["status"] == "SKIPPED_NO_PROFILE"


async def test_sync_failure_keeps_financials_and_counts_failures(session_factory):
    quality: list[int] = []
    async with session_factory() as session:
        company = await _company(session, "FAIL", cik="1045810")
        session.add(
            FinancialMetric(
                company_id=company.id,
                fiscal_year=2020,
                fiscal_period="FY",
                source=SEC_SOURCE,
                revenue=Decimal("10"),
            )
        )
        await session.commit()
        with pytest.raises(SecClientError):
            await execute_sec_sync(
                session,
                company.id,
                client=FakeSec(error=SecClientError("EDGAR unavailable", status_code=503)),
                enqueue_quality_job=_record(quality),
                enqueue_opportunity_job=_record([]),
            )
        with pytest.raises(SecClientError):
            await execute_sec_sync(
                session,
                company.id,
                client=FakeSec(error=SecClientError("EDGAR unavailable", status_code=503)),
                enqueue_quality_job=_record(quality),
                enqueue_opportunity_job=_record([]),
            )
        status = (
            await session.execute(
                select(CompanySyncStatus).where(CompanySyncStatus.company_id == company.id)
            )
        ).scalar_one()
        remaining = await session.scalar(
            select(func.count()).select_from(FinancialMetric).where(FinancialMetric.company_id == company.id)
        )

    assert quality == []
    assert status.consecutive_failures == 2
    assert status.last_success_at is None
    assert status.last_error_at is not None
    assert status.last_error_message == "EDGAR unavailable"
    assert remaining == 1


async def test_due_selection_respects_freshness_and_max(session_factory, job_redis, monkeypatch):
    monkeypatch.setattr(settings, "sec_sync_interval_hours", 24)
    monkeypatch.setattr(settings, "sec_sync_max_companies_per_run", 2)
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        fresh = await _company(session, "FRESH", cik="1000001")
        stale = await _company(session, "STALE", cik="1000002")
        missing = await _company(session, "NONE", cik="1000003")
        extra = await _company(session, "EXTRA", cik="1000004")
        await _company(session, "NOCIK", cik=None)
        session.add_all(
            [
                CompanySyncStatus(
                    company_id=fresh.id,
                    source=SEC_SOURCE,
                    last_success_at=now,
                    consecutive_failures=0,
                ),
                CompanySyncStatus(
                    company_id=stale.id,
                    source=SEC_SOURCE,
                    last_success_at=now - timedelta(hours=25),
                    consecutive_failures=1,
                ),
            ]
        )
        await session.commit()
        result = await enqueue_selected(session)

    assert result["selected"] == 3
    assert result["enqueued"] == 2
    assert result["already_active"] == 0
    assert sec_job_id(fresh.id) not in result["job_ids"]
    assert len(result["job_ids"]) == 2
    assert sec_job_id(stale.id) in result["job_ids"] or sec_job_id(missing.id) in result["job_ids"]
    assert job_redis.exists(f"rq:job:{result['job_ids'][0]}")
    assert extra.id  # the third due company stays for a later run


async def test_second_enqueue_does_not_duplicate(client, job_redis):
    headers = await _headers(client, email="jobs-dup@example.com")
    created = await client.post(
        "/companies",
        json={"name": "Queue Corp", "ticker": "QUEU", "sec_cik": "1045810"},
        headers=headers,
    )
    company_id = created.json()["id"]
    first = await client.post(f"/admin/jobs/sec-sync/{company_id}", headers=headers)
    second = await client.post(f"/admin/jobs/sec-sync/{company_id}", headers=headers)
    detail = await client.get(f"/admin/jobs/{first.json()['job_id']}", headers=headers)

    assert first.status_code == 202
    assert first.json()["job_id"] == sec_job_id(company_id)
    assert first.json()["enqueued"] is True
    assert second.status_code == 200
    assert second.json()["enqueued"] is False
    assert second.json()["job_id"] == first.json()["job_id"]
    assert detail.status_code == 200
    assert detail.json()["status"] == "queued"
    assert "args" not in detail.json()
    assert detail.json()["result"] is None


async def test_job_routes_require_jwt_and_report_status(client, job_redis):
    for path in (
        "/admin/jobs/sec-sync/1",
        "/admin/jobs/sec-sync-due",
        "/admin/jobs/status",
        "/admin/jobs/missing-job",
    ):
        method = client.get if "status" in path or path.endswith("missing-job") else client.post
        response = await method(path)
        assert response.status_code == 401

    headers = await _headers(client, email="jobs-api@example.com")
    missing = await client.post("/admin/jobs/sec-sync/999999", headers=headers)
    created = await client.post(
        "/companies",
        json={"name": "No CIK Corp", "ticker": "NOCK"},
        headers=headers,
    )
    no_cik = await client.post(f"/admin/jobs/sec-sync/{created.json()['id']}", headers=headers)
    unknown = await client.get("/admin/jobs/does-not-exist", headers=headers)
    summary = await client.get("/admin/jobs/status", headers=headers)
    admin = await client.get("/admin/status", headers=headers)

    assert missing.status_code == 404
    assert no_cik.status_code == 400
    assert unknown.status_code == 404
    assert summary.status_code == 200
    assert set(summary.json()) == {"queued", "started", "finished_recent", "failed"}
    assert all(isinstance(value, int) for value in summary.json().values())
    assert admin.status_code == 200
    body = admin.json()
    assert set(body["jobs"]) == {"queued", "started", "finished_recent", "failed"}
    assert set(body["sync"]) == {"sec_due", "sec_last_success", "sec_failures"}
    assert isinstance(body["jobs"]["queued"], int)
    assert isinstance(body["sync"]["sec_due"], int)
    assert isinstance(body["sync"]["sec_failures"], int)


async def test_due_route_enqueues_without_running_imports(client, session_factory, job_redis, monkeypatch):
    monkeypatch.setattr(settings, "sec_sync_max_companies_per_run", 50)
    headers = await _headers(client, email="jobs-due@example.com")
    created = await client.post(
        "/companies",
        json={"name": "Due Corp", "ticker": "DUEC", "sec_cik": "1000099"},
        headers=headers,
    )
    response = await client.post("/admin/jobs/sec-sync-due", headers=headers)
    body = response.json()
    company_id = created.json()["id"]

    assert response.status_code == 200
    assert body["enqueued"] == 1
    assert body["job_ids"] == [sec_job_id(company_id)]
    async with session_factory() as session:
        metrics = await session.scalar(select(func.count()).select_from(FinancialMetric))
    assert metrics == 0


def test_retry_limits_and_scheduler_does_not_import(monkeypatch):
    sec = sec_retry()
    score = score_retry()
    assert sec.max == 2
    assert list(sec.intervals) == [60, 300]
    assert score.max == 1
    assert list(score.intervals) == [30]

    called = {}

    def fake_enqueue():
        called["scan"] = True
        return {"selected": 0, "enqueued": 0, "already_active": 0, "job_ids": []}

    monkeypatch.setattr(scheduler, "enqueue_due_sec_syncs", fake_enqueue)
    assert scheduler.run_scan()["enqueued"] == 0
    assert called["scan"] is True
    source = inspect.getsource(scheduler)
    assert "sync_company_sec" not in source
    assert "sync_sec_financials" not in source
    worker_source = inspect.getsource(worker_main)
    assert worker_source.index("QUEUE_ANALYSIS") < worker_source.index("QUEUE_SEC")


def test_job_errors_do_not_keep_connection_strings():
    message = brief_error("sqlalchemy.exc.OperationalError: postgresql://sentinel:secret-pass@postgres/sentinel")
    assert message == "Job failed"
    assert "secret-pass" not in message
    assert brief_error(SecClientError("EDGAR unavailable", status_code=503)) == "EDGAR unavailable"
