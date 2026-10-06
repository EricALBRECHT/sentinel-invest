"""Load GPU worker presence for the API and the admin page."""

import logging

from app.jobs.gpu.registry import get_worker, list_workers
from app.jobs.queues import redis_connection

logger = logging.getLogger("sentinel.gpu")


def list_worker_records() -> list[dict]:
    return list_workers(redis_connection())


def get_worker_record(name: str) -> dict | None:
    return get_worker(redis_connection(), name)


def gpu_workers_for_page() -> list[dict]:
    try:
        return list_worker_records()
    except Exception as exc:
        logger.warning("gpu_worker_list_failed error_type=%s", type(exc).__name__)
        return []
