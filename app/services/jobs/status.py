"""Queue registries and a public view of one job. Arguments are not returned."""

import logging

from rq import Queue
from rq.exceptions import NoSuchJobError
from rq.job import Job
from rq.registry import FailedJobRegistry, FinishedJobRegistry, ScheduledJobRegistry, StartedJobRegistry

from app.jobs.queues import QUEUE_NAMES, redis_connection
from app.services.jobs.errors import brief_error

logger = logging.getLogger("sentinel.jobs")

_PUBLIC_RESULT_KEYS = frozenset(
    {
        "company_id",
        "ticker",
        "sync",
        "status",
        "periods_found",
        "created",
        "updated",
        "skipped",
        "deleted",
        "quality_job_enqueued",
        "opportunity_job_enqueued",
        "quality_score",
        "opportunity_score",
        "selected",
        "enqueued",
        "already_active",
        "companies",
        "changed",
        "priority_recalculated",
        "last_trade_date",
        "symbol",
        "bars",
        "market_cap_updated",
        "opportunity_recalculated",
        "technical_job_enqueued",
        "technical_score",
        "technical_confidence",
        "method",
        "dates",
        "as_of_date",
        "investment_view_job_enqueued",
        "long_term_conviction_score",
        "entry_attractiveness_score",
        "analysis_readiness_score",
        "duplicates",
        "events",
        "sources",
        "errors",
        "document_id",
        "relationships",
        "candidates",
        "documents",
        "candidate_id",
        "verification_status",
        "entity_type",
        "promoted_company_id",
        "discovery_pipeline_status",
        "discovery_depth",
        "market_symbol",
        "sec_sync_enqueued",
        "market_sync_enqueued",
        "worker",
        "gpu_available",
        "gpu_name",
        "gpu_memory_total_mb",
        "gpu_memory_free_mb",
        "cuda_visible",
        "timestamp",
        "analysis_id",
        "run_id",
        "runtime",
    }
)


def collect_job_counts() -> dict[str, int] | None:
    try:
        connection = redis_connection()
        queued = started = finished = failed = 0
        for name in QUEUE_NAMES:
            queue = Queue(name, connection=connection)
            queued += _as_int(queue.count)
            started += _as_int(StartedJobRegistry(name, connection=connection).count)
            finished += _as_int(FinishedJobRegistry(name, connection=connection).count)
            failed += _as_int(FailedJobRegistry(name, connection=connection).count)
    except Exception as exc:
        logger.warning("job_registry_count_failed error_type=%s", type(exc).__name__)
        return None
    return {
        "queued": queued,
        "started": started,
        "finished_recent": finished,
        "failed": failed,
    }


def describe_job(job_id: str) -> dict | None:
    connection = redis_connection()
    try:
        job = Job.fetch(job_id, connection=connection)
    except NoSuchJobError:
        return None
    status = job.get_status(refresh=True)
    meta = job.meta or {}
    detail = {
        "job_id": job.id,
        "queue": job.origin,
        "status": status,
        "enqueued_at": _iso(job.enqueued_at),
        "started_at": _iso(job.started_at),
        "ended_at": _iso(job.ended_at),
        "result": _public_result(job.result) if status == "finished" else None,
        "error": brief_error(job.exc_info) if status == "failed" and job.exc_info else None,
        "document_id": meta.get("document_id"),
        "run_id": meta.get("run_id"),
        "force": meta.get("force"),
        "retries_left": None,
        "scheduled_at": None,
        "previous_error": None,
    }
    retries_left = getattr(job, "retries_left", None)
    if retries_left is not None:
        detail["retries_left"] = int(retries_left)
    if status == "scheduled":
        detail["scheduled_at"] = _scheduled_at(job, connection)
        detail["previous_error"] = brief_error(job.exc_info) if job.exc_info else None
    return detail


def _scheduled_at(job: Job, connection) -> str | None:
    try:
        queue_name = job.origin or "default"
        registry = ScheduledJobRegistry(queue_name, connection=connection)
        score = registry.get_schedule_time(job.id) if hasattr(registry, "get_schedule_time") else None
        if score is None and hasattr(registry, "get_expiration_time"):
            score = registry.get_expiration_time(job)
        if score is None:
            # RQ stores score as unix timestamp in the sorted set.
            raw = connection.zscore(registry.key, job.id)
            if raw is None:
                return None
            from datetime import datetime, timezone

            return datetime.fromtimestamp(float(raw), tz=timezone.utc).isoformat()
        if hasattr(score, "isoformat"):
            return score.isoformat()
        return str(score)
    except Exception:
        return None


def _public_result(result) -> dict | None:
    if not isinstance(result, dict):
        return None
    return {key: result[key] for key in _PUBLIC_RESULT_KEYS if key in result}


def _as_int(value) -> int:
    if callable(value):
        value = value()
    return int(value)


def _iso(value) -> str | None:
    if value is None:
        return None
    return value.isoformat()
