"""Daily universe priority refresh. The scheduler only enqueues this function."""

import logging

from app.jobs.runner import job_session, run_async
from app.services.universe.bootstrap import bootstrap_universe_data
from app.services.universe.manager import recalculate_universe_priorities as recalculate_all
from app.services.universe.refresh import refresh_nasdaq100, refresh_sp500

logger = logging.getLogger("sentinel.jobs")


def recalculate_universe_priorities() -> dict:
    logger.info("job_started name=recalculate_universe_priorities")
    try:
        result = run_async(_run_priorities)
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


def refresh_universe_members() -> dict:
    logger.info("job_started name=refresh_universe_members")
    try:
        result = run_async(_run_members_refresh)
    except Exception as exc:
        logger.warning("job_failed name=refresh_universe_members error_type=%s", type(exc).__name__)
        raise
    logger.info(
        "job_finished name=refresh_universe_members sp500=%s nasdaq100=%s",
        result["SP500"]["fetched"],
        result["NASDAQ100"]["fetched"],
    )
    return result


def bootstrap_universe_data_job() -> dict:
    logger.info("job_started name=bootstrap_universe_data")
    try:
        result = run_async(_run_bootstrap)
    except Exception as exc:
        logger.warning("job_failed name=bootstrap_universe_data error_type=%s", type(exc).__name__)
        raise
    logger.info(
        "job_finished name=bootstrap_universe_data selected=%s market=%s sec=%s",
        result["selected"],
        result["market_enqueued"],
        result["sec_enqueued"],
    )
    return result


async def _run_priorities() -> dict:
    async with job_session() as session:
        return await recalculate_all(session)


async def _run_members_refresh() -> dict:
    async with job_session() as session:
        sp500 = await refresh_sp500(session)
    async with job_session() as session:
        nasdaq = await refresh_nasdaq100(session)
    return {"SP500": sp500, "NASDAQ100": nasdaq}


async def _run_bootstrap() -> dict:
    async with job_session() as session:
        return await bootstrap_universe_data(session)
