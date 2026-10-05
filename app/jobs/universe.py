"""Daily universe priority refresh. The scheduler only enqueues this function."""

import logging

from app.jobs.runner import job_session, run_async
from app.services.universe.manager import recalculate_universe_priorities as recalculate_all

logger = logging.getLogger("sentinel.jobs")


def recalculate_universe_priorities() -> dict:
    logger.info("job_started name=recalculate_universe_priorities")
    try:
        result = run_async(_run)
    except Exception as exc:
        logger.warning(
            "job_failed name=recalculate_universe_priorities error_type=%s",
            type(exc).__name__,
        )
        raise
    logger.info(
        "job_finished name=recalculate_universe_priorities companies=%s changed=%s",
        result["companies"],
        result["changed"],
    )
    return result


async def _run() -> dict:
    async with job_session() as session:
        return await recalculate_all(session)
