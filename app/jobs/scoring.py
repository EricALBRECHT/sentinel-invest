"""Score jobs. They call the existing recalculation services and add no formula of their own."""

import logging

from app.jobs.investment import schedule_investment_view_after
from app.jobs.runner import job_session, run_async
from app.services.jobs.scoring import execute_opportunity, execute_quality

logger = logging.getLogger("sentinel.jobs")


def recalculate_quality(company_id: int) -> dict:
    return _run("recalculate_quality", company_id, execute_quality)


def recalculate_opportunity(company_id: int) -> dict:
    return _run("recalculate_opportunity", company_id, execute_opportunity)


def _run(name: str, company_id: int, function) -> dict:
    logger.info("job_started name=%s company_id=%s", name, company_id)
    try:
        result = run_async(lambda: _call(function, company_id))
    except Exception as exc:
        logger.warning(
            "job_failed name=%s company_id=%s error_type=%s",
            name,
            company_id,
            type(exc).__name__,
        )
        raise
    result = dict(result)
    result["investment_view_job_enqueued"] = schedule_investment_view_after(result)
    logger.info("job_finished name=%s company_id=%s status=%s", name, company_id, result.get("status"))
    return result


async def _call(function, company_id: int) -> dict:
    async with job_session() as session:
        return await function(session, company_id)
