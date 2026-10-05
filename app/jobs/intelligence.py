"""Intelligence jobs. They enqueue or fetch. They do not change investment scores."""

import logging

from app.core.config import settings
from app.jobs.queues import enqueue_news_sync
from app.jobs.runner import job_session, run_async
from app.services.intelligence.ingestion import ingest_source, process_document, sync_company_news
from app.services.intelligence.schedule import select_due_news_company_ids

logger = logging.getLogger("sentinel.jobs")


def sync_news_company(company_id: int, force: bool = False) -> dict:
    return _run("sync_news_company", lambda: _sync(company_id, force))


def poll_external_source(source_id: int) -> dict:
    return _run("poll_external_source", lambda: _poll(source_id))


def process_intelligence_document(document_id: int) -> dict:
    return _run("process_intelligence_document", lambda: _process(document_id))


def enqueue_due_news_syncs() -> dict:
    logger.info("job_started name=enqueue_due_news_syncs")
    try:
        result = run_async(_enqueue)
    except Exception as exc:
        logger.warning("job_failed name=enqueue_due_news_syncs error_type=%s", type(exc).__name__)
        raise
    logger.info(
        "job_finished name=enqueue_due_news_syncs selected=%s enqueued=%s",
        result.get("selected"),
        result.get("enqueued"),
    )
    return result


def _run(name: str, function) -> dict:
    logger.info("job_started name=%s", name)
    try:
        result = run_async(function)
    except Exception as exc:
        logger.warning("job_failed name=%s error_type=%s", name, type(exc).__name__)
        raise
    logger.info("job_finished name=%s status=%s", name, result.get("status"))
    return result


async def _sync(company_id: int, force: bool) -> dict:
    async with job_session() as session:
        return await sync_company_news(session, company_id, force=force)


async def _poll(source_id: int) -> dict:
    async with job_session() as session:
        return await ingest_source(session, source_id)


async def _process(document_id: int) -> dict:
    async with job_session() as session:
        return await process_document(session, document_id)


async def _enqueue() -> dict:
    async with job_session() as session:
        company_ids = await select_due_news_company_ids(session)
    limit = max(0, settings.intelligence_max_companies_per_run)
    enqueued = 0
    already_active = 0
    job_ids: list[str] = []
    for company_id in company_ids:
        if enqueued >= limit:
            break
        queued = enqueue_news_sync(company_id, force=False)
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
