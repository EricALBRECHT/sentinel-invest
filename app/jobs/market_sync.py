"""Market sync jobs. One Redis lock keeps provider requests serial."""

import logging

from app.core.config import settings
from app.jobs.queues import enqueue_market_sync, redis_connection
from app.jobs.runner import job_session, run_async
from app.jobs.technical import schedule_technical_after_market
from app.services.market.schedule import select_due_market_company_ids
from app.services.market.sync import execute_market_sync

logger = logging.getLogger("sentinel.jobs")


def sync_company_market(company_id: int) -> dict:
    logger.info("job_started name=sync_company_market company_id=%s", company_id)
    connection = redis_connection()
    lock = connection.lock("sentinel:market-fetch", timeout=180, blocking_timeout=180)
    if not lock.acquire(blocking=True):
        logger.warning("market_fetch_lock_unavailable company_id=%s", company_id)
        raise TimeoutError("Market fetch is already running")
    try:
        result = run_async(lambda: _run(company_id))
    except Exception as exc:
        logger.warning(
            "job_failed name=sync_company_market company_id=%s error_type=%s",
            company_id,
            type(exc).__name__,
        )
        raise
    else:
        result = dict(result)
        result["technical_job_enqueued"] = schedule_technical_after_market(result)
        logger.info(
            "job_finished name=sync_company_market company_id=%s sync=%s",
            company_id,
            result.get("sync"),
        )
        return result
    finally:
        _release(lock)


def enqueue_due_market_syncs() -> dict:
    """Create market jobs for due universe companies. This does not fetch prices."""
    logger.info("job_started name=enqueue_due_market_syncs")
    try:
        result = run_async(_enqueue)
    except Exception as exc:
        logger.warning("job_failed name=enqueue_due_market_syncs error_type=%s", type(exc).__name__)
        raise
    logger.info(
        "job_finished name=enqueue_due_market_syncs selected=%s enqueued=%s",
        result["selected"],
        result["enqueued"],
    )
    return result


async def enqueue_selected_market(session) -> dict:
    company_ids = await select_due_market_company_ids(session)
    limit = max(0, settings.market_sync_max_companies_per_run)
    enqueued = 0
    already_active = 0
    job_ids: list[str] = []
    for company_id in company_ids:
        if enqueued >= limit:
            break
        queued = enqueue_market_sync(company_id)
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


async def _run(company_id: int) -> dict:
    async with job_session() as session:
        return await execute_market_sync(session, company_id)


async def _enqueue() -> dict:
    async with job_session() as session:
        return await enqueue_selected_market(session)


def _release(lock) -> None:
    try:
        lock.release()
    except Exception as exc:
        logger.warning("market_fetch_lock_release_failed error_type=%s", type(exc).__name__)
