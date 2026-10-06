"""RQ process for the gpu queue only. Business queues stay on sentinel-core."""

import logging
import os
import threading

from redis.exceptions import RedisError
from rq import Worker

from app.jobs.gpu.connection import redis_from_env
from app.jobs.gpu.probe import heartbeat_payload
from app.jobs.gpu.registry import HEARTBEAT_INTERVAL_SECONDS, record_heartbeat

logger = logging.getLogger("sentinel.gpu")
GPU_QUEUE_NAMES = ("gpu",)
_CORE_QUEUES = ("default", "sec", "analysis", "market", "intelligence")


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(levelname)s %(name)s %(message)s",
    )
    queue_name = os.environ.get("GPU_QUEUE", "gpu")
    if queue_name != "gpu" or queue_name in _CORE_QUEUES:
        raise RuntimeError("GPU worker listens only to the gpu queue")
    stop = threading.Event()
    beater = threading.Thread(
        target=_beat,
        args=(redis_from_env(), stop),
        name="gpu-heartbeat",
        daemon=True,
    )
    beater.start()
    logger.info("gpu_worker_started queue=%s", queue_name)
    try:
        worker = Worker([queue_name], connection=redis_from_env())
        worker.work(with_scheduler=False, logging_level=os.environ.get("LOG_LEVEL", "INFO"))
    except RedisError as exc:
        logger.warning("gpu_worker_redis_failed error_type=%s", type(exc).__name__)
        raise
    finally:
        stop.set()


def _beat(connection, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            record_heartbeat(connection, heartbeat_payload())
        except Exception as exc:
            logger.warning("gpu_heartbeat_failed error_type=%s", type(exc).__name__)
        stop.wait(HEARTBEAT_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
