"""Job entry points. Business decisions live in app.services.jobs."""

import logging

from app.jobs.runner import job_session, run_async
from app.jobs.queues import redis_connection
from app.services.jobs.sec_sync import execute_sec_sync

logger = logging.getLogger("sentinel.jobs")


def sync_company_sec(company_id: int) -> dict:
    logger.info("job_started name=sync_company_sec company_id=%s", company_id)
    connection = redis_connection()
    lock = connection.lock("sentinel:sec-fetch", timeout=900, blocking_timeout=900)
    acquired = lock.acquire(blocking=True)
    if not acquired:
        logger.warning("sec_fetch_lock_unavailable company_id=%s", company_id)
        raise TimeoutError("SEC fetch is already running")
    try:
        result = run_async(lambda: _run(company_id))
    except Exception as exc:
        logger.warning(
            "job_failed name=sync_company_sec company_id=%s error_type=%s",
            company_id,
            type(exc).__name__,
        )
        raise
    else:
        logger.info(
            "job_finished name=sync_company_sec company_id=%s sync=%s",
            company_id,
            result.get("sync"),
        )
        return result
    finally:
        _release(lock)


def _release(lock) -> None:
    try:
        lock.release()
    except Exception as exc:
        logger.warning("sec_fetch_lock_release_failed error_type=%s", type(exc).__name__)


async def _run(company_id: int) -> dict:
    async with job_session() as session:
        return await execute_sec_sync(session, company_id)
