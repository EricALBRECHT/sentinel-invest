"""Technical timing jobs. The formula lives in the technical service."""

from datetime import date
import logging

from app.jobs.investment import schedule_investment_view_after
from app.jobs.queues import enqueue_technical
from app.jobs.runner import job_session, run_async
from app.services.technical.service import backfill_technical_history, recalculate_technical_snapshot

logger = logging.getLogger("sentinel.jobs")
_ACTIVE = frozenset({"queued", "started", "deferred", "scheduled"})


def recalculate_technical(company_id: int) -> dict:
    logger.info("job_started name=recalculate_technical company_id=%s", company_id)
    try:
        result = run_async(lambda: _call(company_id))
    except Exception as exc:
        logger.warning(
            "job_failed name=recalculate_technical company_id=%s error_type=%s",
            company_id,
            type(exc).__name__,
        )
        raise
    result = dict(result)
    result["investment_view_job_enqueued"] = schedule_investment_view_after(result)
    logger.info(
        "job_finished name=recalculate_technical company_id=%s status=%s",
        company_id,
        result.get("status"),
    )
    return result


def backfill_technical(
    company_id: int,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    logger.info("job_started name=backfill_technical company_id=%s", company_id)
    start = date.fromisoformat(start_date) if start_date else None
    end = date.fromisoformat(end_date) if end_date else None
    try:
        result = run_async(lambda: _backfill(company_id, start, end))
    except Exception as exc:
        logger.warning(
            "job_failed name=backfill_technical company_id=%s error_type=%s",
            company_id,
            type(exc).__name__,
        )
        raise
    logger.info(
        "job_finished name=backfill_technical company_id=%s status=%s dates=%s",
        company_id,
        result.get("status"),
        result.get("dates"),
    )
    return result


def schedule_technical_after_market(result: dict) -> bool:
    """Enqueue a technical job after a price sync that stored or corrected a session."""
    if result.get("sync") != "success" or result.get("company_id") is None:
        return False
    if "created" in result or "updated" in result:
        created = int(result.get("created") or 0)
        updated = int(result.get("updated") or 0)
        if created == 0 and updated == 0:
            return False
    try:
        queued = enqueue_technical(int(result["company_id"]))
    except Exception as exc:
        logger.warning(
            "technical_enqueue_failed company_id=%s error_type=%s",
            result.get("company_id"),
            type(exc).__name__,
        )
        return False
    if not isinstance(queued, dict):
        return bool(queued)
    return bool(queued.get("enqueued") or queued.get("status") in _ACTIVE)


async def _backfill(company_id: int, start: date | None, end: date | None) -> dict:
    async with job_session() as session:
        return await backfill_technical_history(session, company_id, start, end)


async def _call(company_id: int) -> dict:
    async with job_session() as session:
        row = await recalculate_technical_snapshot(session, company_id)
    if row is None:
        return {"company_id": company_id, "status": "no_history", "technical_score": None, "technical_confidence": None}
    status = "success" if row.technical_score is not None else "insufficient_history"
    return {
        "company_id": company_id,
        "status": status,
        "technical_score": None if row.technical_score is None else format(row.technical_score, "f"),
        "technical_confidence": row.technical_confidence,
    }
