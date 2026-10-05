"""Supply-chain jobs. They enqueue or read stored documents. They do not change scores."""

import logging

from app.core.config import settings
from app.jobs.queues import enqueue_supply_chain
from app.jobs.runner import job_session, run_async
from app.services.supply_chain.extraction import process_company_documents, select_due_company_ids

logger = logging.getLogger("sentinel.jobs")


def process_supply_chain_company(company_id: int) -> dict:
    return _run(company_id)


def enqueue_due_supply_chain() -> dict:
    logger.info("job_started name=enqueue_due_supply_chain")
    try:
        result = run_async(_enqueue)
    except Exception as exc:
        logger.warning("job_failed name=enqueue_due_supply_chain error_type=%s", type(exc).__name__)
        raise
    logger.info(
        "job_finished name=enqueue_due_supply_chain selected=%s enqueued=%s",
        result.get("selected"),
        result.get("enqueued"),
    )
    return result


def _run(company_id: int) -> dict:
    logger.info("job_started name=process_supply_chain_company company_id=%s", company_id)
    try:
        result = run_async(lambda: _process(company_id))
    except Exception as exc:
        logger.warning(
            "job_failed name=process_supply_chain_company error_type=%s",
            type(exc).__name__,
        )
        raise
    logger.info(
        "job_finished name=process_supply_chain_company status=%s",
        result.get("status"),
    )
    return result


async def _process(company_id: int) -> dict:
    async with job_session() as session:
        return await process_company_documents(session, company_id)


async def _enqueue() -> dict:
    async with job_session() as session:
        company_ids = await select_due_company_ids(session)
    limit = max(0, settings.supply_chain_max_companies_per_run)
    enqueued = 0
    already_active = 0
    job_ids: list[str] = []
    for company_id in company_ids:
        if enqueued >= limit:
            break
        queued = enqueue_supply_chain(company_id)
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
