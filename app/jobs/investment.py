"""Investment view jobs. The formula lives in the investment view service."""

import logging

from app.jobs.queues import enqueue_investment_view
from app.jobs.runner import job_session, run_async
from app.services.investment_view.service import recalculate_investment_view

logger = logging.getLogger("sentinel.jobs")
_AFTER = frozenset({"success", "insufficient_history"})


def recalculate_investment(company_id: int) -> dict:
    logger.info("job_started name=recalculate_investment company_id=%s", company_id)
    try:
        result = run_async(lambda: _call(company_id))
    except Exception as exc:
        logger.warning(
            "job_failed name=recalculate_investment company_id=%s error_type=%s",
            company_id,
            type(exc).__name__,
        )
        raise
    logger.info(
        "job_finished name=recalculate_investment company_id=%s status=%s",
        company_id,
        result.get("status"),
    )
    return result


def schedule_investment_view_after(result: dict) -> bool:
    """Enqueue one view after a successful quality, opportunity, or technical recalculation."""
    if result.get("status") not in _AFTER or result.get("company_id") is None:
        return False
    return schedule_investment_view(int(result["company_id"]))


def schedule_investment_view(company_id: int) -> bool:
    try:
        queued = enqueue_investment_view(company_id)
    except Exception as exc:
        logger.warning(
            "investment_view_enqueue_failed company_id=%s error_type=%s",
            company_id,
            type(exc).__name__,
        )
        return False
    if not isinstance(queued, dict):
        return bool(queued)
    return bool(queued.get("enqueued") or queued.get("status") in {"queued", "started", "deferred", "scheduled"})


async def _call(company_id: int) -> dict:
    async with job_session() as session:
        row = await recalculate_investment_view(session, company_id)
    if row is None:
        return {"company_id": company_id, "status": "missing_company", "method": None}
    return {
        "company_id": company_id,
        "status": "success",
        "method": row.method,
        "as_of_date": row.as_of_date.isoformat(),
        "long_term_conviction_score": _text(row.long_term_conviction_score),
        "entry_attractiveness_score": _text(row.entry_attractiveness_score),
        "analysis_readiness_score": _text(row.analysis_readiness_score),
    }


def _text(value) -> str | None:
    if value is None:
        return None
    return format(value, "f")
