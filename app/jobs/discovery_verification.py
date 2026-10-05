"""Candidate verification jobs. They read structured identifiers. They do not change scores."""

import logging

from app.core.config import settings
from app.jobs.queues import enqueue_verify_candidate
from app.jobs.runner import job_session, run_async
from app.services.discovery_verification.verifier import select_due_candidate_ids, verify_candidate

logger = logging.getLogger("sentinel.jobs")


def verify_candidate_job(candidate_id: int) -> dict:
    logger.info("job_started name=verify_candidate_job candidate_id=%s", candidate_id)
    try:
        result = run_async(lambda: _verify(candidate_id))
    except Exception as exc:
        logger.warning("job_failed name=verify_candidate_job error_type=%s", type(exc).__name__)
        raise
    logger.info("job_finished name=verify_candidate_job status=%s", result.get("verification_status"))
    return result


def enqueue_due_discovery_verifications() -> dict:
    logger.info("job_started name=enqueue_due_discovery_verifications")
    try:
        result = run_async(_enqueue)
    except Exception as exc:
        logger.warning(
            "job_failed name=enqueue_due_discovery_verifications error_type=%s",
            type(exc).__name__,
        )
        raise
    logger.info(
        "job_finished name=enqueue_due_discovery_verifications selected=%s enqueued=%s",
        result.get("selected"),
        result.get("enqueued"),
    )
    return result


async def _verify(candidate_id: int) -> dict:
    async with job_session() as session:
        row = await verify_candidate(session, candidate_id)
    if row is None:
        return {
            "candidate_id": candidate_id,
            "verification_status": "missing",
            "entity_type": None,
            "promoted_company_id": None,
        }
    return {
        "candidate_id": row.id,
        "verification_status": row.verification_status,
        "entity_type": row.entity_type,
        "promoted_company_id": row.promoted_company_id,
    }


async def _enqueue() -> dict:
    async with job_session() as session:
        candidate_ids = await select_due_candidate_ids(session)
    limit = max(0, settings.discovery_verify_max_per_run)
    enqueued = 0
    already_active = 0
    job_ids: list[str] = []
    for candidate_id in candidate_ids:
        if enqueued >= limit:
            break
        queued = enqueue_verify_candidate(candidate_id)
        job_ids.append(queued["job_id"])
        if queued["enqueued"]:
            enqueued += 1
        else:
            already_active += 1
    return {
        "selected": len(candidate_ids),
        "enqueued": enqueued,
        "already_active": already_active,
        "job_ids": job_ids,
    }
