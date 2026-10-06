"""RQ process for the gpu queue only. Business queues stay on sentinel-core.

The GPU worker is one long-lived process:

- it loads the GGUF once
- it initializes CUDA once
- it keeps that model warm
- it runs ``gpu`` jobs one after another in this same process

RQ's standard Worker calls ``os.fork()`` to create a work-horse after that
initialization. A CUDA context inherited by the child aborts inside
``llama_decode``. SimpleWorker runs the job in this process, so nothing is
forked after CUDA starts.

Timeouts stay with RQ's Linux SIGALRM death penalty. It raises
``JobTimeoutException`` in this process; it does not fork and it does not
send SIGKILL. The core enqueues GPU work with a 900 second job timeout and
stops waiting on its side after the same delay. Restarting this container is
an explicit last resort, not something the worker does to itself.
"""

import logging
import os
import threading

from redis.exceptions import RedisError
from rq import SimpleWorker

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
    logger.info("gpu_worker_mode=simple")
    logger.info("fork_enabled=false")
    stop = threading.Event()
    beater = threading.Thread(
        target=_beat,
        args=(redis_from_env(), stop),
        name="gpu-heartbeat",
        daemon=True,
    )
    beater.start()
    _preload()
    logger.info("gpu_worker_starting queue=%s", queue_name)
    try:
        worker = SimpleWorker(
            [queue_name],
            connection=redis_from_env(),
            name=os.environ.get("GPU_WORKER_NAME", "sentinel-gpu-01"),
        )
        logger.info("gpu_worker_started queue=%s", queue_name)
        worker.work(with_scheduler=False, logging_level=os.environ.get("LOG_LEVEL", "INFO"))
    except RedisError as exc:
        logger.warning("gpu_worker_redis_failed error_type=%s", type(exc).__name__)
        raise
    finally:
        stop.set()


def _preload() -> None:
    """Load the GGUF if possible. Failure must not keep RQ from starting."""
    try:
        from app.jobs.gpu.model_runtime import preload_model

        preload_model()
    except Exception:
        logger.exception("ai_preload_failed")


def _beat(connection, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            record_heartbeat(connection, heartbeat_payload())
        except Exception as exc:
            logger.warning("gpu_heartbeat_failed error_type=%s", type(exc).__name__)
        stop.wait(HEARTBEAT_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
