"""RQ worker. One process runs one job at a time, so SEC fetches stay serial."""

import logging

from redis.exceptions import RedisError
from rq import Worker

from app.jobs.logging import configure_job_logging
from app.jobs.queues import (
    QUEUE_ANALYSIS,
    QUEUE_DEFAULT,
    QUEUE_INTELLIGENCE,
    QUEUE_MARKET,
    QUEUE_SEC,
    redis_connection,
)

logger = logging.getLogger("sentinel.jobs")


def main() -> None:
    configure_job_logging()
    connection = redis_connection()
    # Analysis and maintenance are checked before market data, then SEC.
    queues = [QUEUE_ANALYSIS, QUEUE_INTELLIGENCE, QUEUE_DEFAULT, QUEUE_MARKET, QUEUE_SEC]
    logger.info("worker_started queues=%s", ",".join(queues))
    try:
        worker = Worker(queues, connection=connection)
        # The worker thread only releases delayed RQ retries. Daily scans stay in the scheduler process.
        worker.work(with_scheduler=True, logging_level="INFO")
    except RedisError as exc:
        logger.warning("worker_redis_failed error_type=%s", type(exc).__name__)
        raise


if __name__ == "__main__":
    main()
