"""Periodic scan. It only enqueues due SEC jobs; the worker performs the fetches."""

import logging
import time

from app.core.config import settings
from app.jobs.logging import configure_job_logging
from app.jobs.maintenance import enqueue_due_sec_syncs
from app.jobs.market_sync import enqueue_due_market_syncs
from app.jobs.queues import enqueue_universe_priorities

logger = logging.getLogger("sentinel.jobs")


def run_scan() -> dict:
    """One SEC scheduler tick. Imports are not executed here."""
    return enqueue_due_sec_syncs()


def run_priority_scan() -> dict:
    """One universe scheduler tick. Priorities are recalculated by the worker."""
    return enqueue_universe_priorities()


def run_market_scan() -> dict:
    """One market scheduler tick. Prices are fetched by the worker."""
    return enqueue_due_market_syncs()


def main() -> None:
    configure_job_logging()
    sec_interval = max(1, settings.sec_sync_scan_hours) * 3600
    priority_interval = max(1, settings.universe_priority_interval_hours) * 3600
    market_interval = max(1, settings.market_sync_scan_hours) * 3600
    logger.info(
        "scheduler_started scan_interval_seconds=%s sync_interval_hours=%s max_companies=%s priority_interval_seconds=%s market_interval_seconds=%s",
        sec_interval,
        settings.sec_sync_interval_hours,
        settings.sec_sync_max_companies_per_run,
        priority_interval,
        market_interval,
    )
    # The first automatic pass waits one full interval so a restart does not
    # immediately refetch SEC filings, prices, or rewrite priorities.
    next_sec = time.monotonic() + sec_interval
    next_priority = time.monotonic() + priority_interval
    next_market = time.monotonic() + market_interval
    while True:
        now = time.monotonic()
        if now >= next_sec:
            try:
                run_scan()
            except Exception as exc:
                logger.warning("scheduler_scan_failed error_type=%s", type(exc).__name__)
            next_sec = time.monotonic() + sec_interval
        if now >= next_priority:
            try:
                run_priority_scan()
            except Exception as exc:
                logger.warning("scheduler_priority_failed error_type=%s", type(exc).__name__)
            next_priority = time.monotonic() + priority_interval
        if now >= next_market:
            try:
                run_market_scan()
            except Exception as exc:
                logger.warning("scheduler_market_failed error_type=%s", type(exc).__name__)
            next_market = time.monotonic() + market_interval
        wait = min(next_sec, next_priority, next_market) - time.monotonic()
        time.sleep(min(5.0, max(0.2, wait)))


if __name__ == "__main__":
    main()
