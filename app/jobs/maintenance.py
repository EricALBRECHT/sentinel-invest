"""Select due SEC companies and enqueue one job each. This module does not call EDGAR."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.jobs.queues import enqueue_sec_sync
from app.jobs.runner import job_session, run_async
from app.services.jobs.schedule import select_due_company_ids

logger = logging.getLogger("sentinel.jobs")


def enqueue_due_sec_syncs() -> dict:
    """Create SEC jobs for companies whose last success is missing or stale."""
    logger.info("job_started name=enqueue_due_sec_syncs")
    try:
        result = run_async(_enqueue)
    except Exception as exc:
        logger.warning("job_failed name=enqueue_due_sec_syncs error_type=%s", type(exc).__name__)
        raise
    logger.info(
        "job_finished name=enqueue_due_sec_syncs selected=%s enqueued=%s",
        result["selected"],
        result["enqueued"],
    )
    return result


async def enqueue_selected(session: AsyncSession) -> dict:
    """Enqueue due companies from an existing session. Active jobs do not consume the budget."""
    company_ids = await select_due_company_ids(session)
    limit = max(0, settings.sec_sync_max_companies_per_run)
    enqueued = 0
    already_active = 0
    job_ids: list[str] = []
    for company_id in company_ids:
        if enqueued >= limit:
            break
        queued = enqueue_sec_sync(company_id)
        job_ids.append(queued["job_id"])
        if queued["enqueued"]:
            enqueued += 1
        else:
            already_active += 1
    return {
        "selected": len(company_ids),
        "enqueued": enqueued,
        "already_active": already_active,
        "job_ids": job_ids,
    }


async def _enqueue() -> dict:
    async with job_session() as session:
        return await enqueue_selected(session)
