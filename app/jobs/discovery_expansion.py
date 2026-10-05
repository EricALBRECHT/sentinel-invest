"""Discovery expansion jobs. They collect structured sources and do not change scores."""

import logging

from app.core.config import settings
from app.jobs.queues import enqueue_discovery_expansion
from app.jobs.runner import job_session, run_async
from app.services.discovery_expansion.expansion import expand_discovered_company
from app.services.discovery_expansion.policy import select_due_expansion_ids

logger = logging.getLogger("sentinel.jobs")


def expand_discovered_company_job(company_id: int) -> dict:
    logger.info("job_started name=expand_discovered_company company_id=%s", company_id)
    try:
        result = run_async(lambda: _expand(company_id))
    except Exception as exc:
        logger.warning(
            "job_failed name=expand_discovered_company company_id=%s error_type=%s",
            company_id,
            type(exc).__name__,
        )
        raise
    logger.info(
        "job_finished name=expand_discovered_company company_id=%s status=%s",
        company_id,
        result.get("status"),
    )
    return result


def enqueue_due_discovery_expansions() -> dict:
    logger.info("job_started name=enqueue_due_discovery_expansions")
    try:
        result = run_async(_enqueue)
    except Exception as exc:
        logger.warning("job_failed name=enqueue_due_discovery_expansions error_type=%s", type(exc).__name__)
        raise
    logger.info(
        "job_finished name=enqueue_due_discovery_expansions selected=%s enqueued=%s",
        result.get("selected"),
        result.get("enqueued"),
    )
    return result


async def _expand(company_id: int) -> dict:
    async with job_session() as session:
        return await expand_discovered_company(session, company_id)


async def _enqueue() -> dict:
    async with job_session() as session:
        company_ids = await select_due_expansion_ids(session)
    limit = max(0, settings.discovery_expansion_max_per_run)
    enqueued = 0
    already_active = 0
    job_ids: list[str] = []
    for company_id in company_ids:
        if enqueued >= limit:
            break
        queued = enqueue_discovery_expansion(company_id)
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
