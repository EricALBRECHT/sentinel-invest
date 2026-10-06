"""Orchestrate AI document analysis on core workers (PostgreSQL + validation)."""

import logging
import time

from rq.exceptions import NoSuchJobError
from rq.job import Job

from app.jobs.queues import QUEUE_GPU, ai_document_gpu_job_id, enqueue_ai_document_analysis, redis_connection
from app.models.ai_document_analysis import AiDocumentAnalysis
from app.jobs.runner import job_session, run_async
from app.services.ai.service import (
    build_document_payload,
    ensure_analysis_row,
    finalize_from_gpu_result,
    get_latest_analysis,
    mark_running,
    run_inference,
)

logger = logging.getLogger("sentinel.jobs")


async def _run(document_id: int, *, force: bool = False) -> dict:
    async with job_session() as session:
        existing = await get_latest_analysis(session, document_id)
        if existing is not None and existing.status == "SUCCESS" and not force:
            await session.commit()
            return {"document_id": document_id, "status": "SUCCESS", "skipped": True, "analysis_id": existing.id}
        try:
            payload = await build_document_payload(session, document_id)
        except (LookupError, ValueError) as exc:
            await session.commit()
            return {"document_id": document_id, "status": "FAILED", "error": str(exc)}
        row = await ensure_analysis_row(session, document_id, force=force)
        await mark_running(session, row)
        row_id = row.id
        await session.commit()
    gpu_result = run_inference(payload.model_dump())
    async with job_session() as session:
        row = await session.get(AiDocumentAnalysis, row_id)
        if row is None:
            row = await ensure_analysis_row(session, document_id, force=True)
        finalized = await finalize_from_gpu_result(session, row, gpu_result)
        await session.commit()
        return {
            "document_id": document_id,
            "status": finalized.status,
            "analysis_id": finalized.id,
            "skipped": False,
            "runtime": finalized.runtime_json,
        }


def run_ai_document_analysis(document_id: int, force: bool = False) -> dict:
    logger.info("job_started name=ai_document_analysis document_id=%s force=%s", document_id, force)
    try:
        result = run_async(lambda: _run(document_id, force=force))
    except Exception as exc:
        logger.warning(
            "job_failed name=ai_document_analysis document_id=%s error_type=%s",
            document_id,
            type(exc).__name__,
        )
        raise
    logger.info(
        "job_finished name=ai_document_analysis document_id=%s status=%s",
        document_id,
        result.get("status"),
    )
    return result


def run_gpu_and_wait(payload: dict, *, timeout: int = 900) -> dict:
    document_id = int(payload["document_id"])
    queued = enqueue_ai_document_gpu(payload)
    if not queued.get("enqueued") and queued.get("status") not in {"finished", "failed"}:
        return wait_for_rq_job(queued["job_id"], timeout=timeout)
    if queued.get("enqueued"):
        return wait_for_rq_job(queued["job_id"], timeout=timeout)
    return wait_for_rq_job(ai_document_gpu_job_id(document_id), timeout=timeout)


def enqueue_ai_document_gpu(payload: dict) -> dict:
    from app.jobs.queues import enqueue_call
    from rq import Retry

    document_id = int(payload["document_id"])
    return enqueue_call(
        QUEUE_GPU,
        "app.jobs.gpu.ai_document.analyze_document_payload",
        payload,
        job_id=ai_document_gpu_job_id(document_id),
        retry=Retry(max=0),
        timeout=900,
    )


def wait_for_rq_job(job_id: str, *, timeout: int = 900) -> dict:
    connection = redis_connection()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            job = Job.fetch(job_id, connection=connection)
        except NoSuchJobError:
            time.sleep(0.5)
            continue
        status = job.get_status(refresh=True)
        if status == "finished":
            result = job.result
            if isinstance(result, dict):
                return result
            raise RuntimeError("GPU job returned a non-object result")
        if status == "failed":
            raise RuntimeError("GPU analysis job failed")
        time.sleep(1)
    raise TimeoutError(f"Timed out waiting for GPU job {job_id}")


def enqueue_ai_document(document_id: int) -> dict:
    return enqueue_ai_document_analysis(document_id)
