"""Run one SEC sync and record it. Score jobs are enqueued only after a successful import."""

from datetime import datetime, timezone
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.jobs.queues import enqueue_opportunity, enqueue_quality
from app.models.company import Company
from app.models.company_sync_status import CompanySyncStatus
from app.models.opportunity_profile import OpportunityProfile
from app.services.jobs.errors import brief_error
from app.services.sec.client import SecClient
from app.services.sec.mappings import SEC_SOURCE
from app.services.sec.sync import sync_sec_financials

logger = logging.getLogger("sentinel.jobs")

_ACTIVE = frozenset({"queued", "started", "deferred", "scheduled"})


async def execute_sec_sync(
    session: AsyncSession,
    company_id: int,
    *,
    client=None,
    enqueue_quality_job=enqueue_quality,
    enqueue_opportunity_job=enqueue_opportunity,
) -> dict:
    company = await session.get(Company, company_id)
    if company is None:
        return {"company_id": company_id, "ticker": None, "sync": "missing_company"}
    ticker = company.ticker
    if not company.sec_cik:
        await _record_failure(session, company_id, "Company has no SEC CIK")
        return _summary(company_id, ticker, "skipped_no_cik", None, False, False)

    await _mark_attempt(session, company_id)
    owns_client = client is None
    if client is None:
        client = SecClient(
            user_agent=settings.sec_user_agent,
            timeout=settings.sec_timeout_seconds,
            max_retries=settings.sec_max_retries,
            min_interval_seconds=settings.sec_min_interval_seconds,
        )
    try:
        result = await sync_sec_financials(session, company, client)
    except Exception as exc:
        await session.rollback()
        await _record_failure(session, company_id, brief_error(exc))
        raise
    finally:
        if owns_client:
            await client.aclose()

    await _record_success(session, company_id, result)
    has_profile = await _has_profile(session, company_id)
    quality_enqueued = _enqueue(enqueue_quality_job, company_id)
    opportunity_enqueued = _enqueue(enqueue_opportunity_job, company_id) if has_profile else False
    return _summary(company_id, ticker, "success", result, quality_enqueued, opportunity_enqueued)


async def _mark_attempt(session: AsyncSession, company_id: int) -> None:
    row = await _status_row(session, company_id)
    row.last_attempt_at = datetime.now(timezone.utc)
    await session.commit()


async def _record_success(session: AsyncSession, company_id: int, result) -> None:
    row = await _status_row(session, company_id)
    row.last_success_at = datetime.now(timezone.utc)
    row.last_error_at = None
    row.last_error_message = None
    row.consecutive_failures = 0
    row.last_result_json = {
        "periods_found": result.periods_found,
        "created": result.created,
        "updated": result.updated,
        "skipped": result.skipped,
        "deleted": result.deleted,
        "warning_count": len(result.warnings),
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
        CompanySyncStatus.source == SEC_SOURCE,
    )
    row = (await session.execute(statement)).scalar_one_or_none()
    if row is None:
        row = CompanySyncStatus(
            company_id=company_id,
            source=SEC_SOURCE,
            consecutive_failures=0,
        )
        session.add(row)
        await session.flush()
    return row


async def _has_profile(session: AsyncSession, company_id: int) -> bool:
    statement = select(OpportunityProfile.id).where(OpportunityProfile.company_id == company_id)
    return (await session.execute(statement)).scalar_one_or_none() is not None


def _enqueue(function, company_id: int) -> bool:
    try:
        queued = function(company_id)
    except Exception as exc:
        logger.warning(
            "score_enqueue_failed company_id=%s error_type=%s",
            company_id,
            type(exc).__name__,
        )
        return False
    if not isinstance(queued, dict):
        return bool(queued)
    return bool(queued.get("enqueued") or queued.get("status") in _ACTIVE)


def _summary(company_id, ticker, sync, result, quality_enqueued, opportunity_enqueued) -> dict:
    return {
        "company_id": company_id,
        "ticker": ticker,
        "sync": sync,
        "periods_found": None if result is None else result.periods_found,
        "created": None if result is None else result.created,
        "updated": None if result is None else result.updated,
        "skipped": None if result is None else result.skipped,
        "deleted": None if result is None else result.deleted,
        "quality_job_enqueued": quality_enqueued,
        "opportunity_job_enqueued": opportunity_enqueued,
    }
