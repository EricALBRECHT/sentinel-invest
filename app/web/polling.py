"""Central HTMX polling intervals for live dashboard and admin panels."""

# Seconds between automatic fragment refreshes (read-only; never enqueues jobs).
POLL_SUMMARY_SECONDS = 60
POLL_COMPANIES_SECONDS = 60
POLL_BOOTSTRAP_SECONDS = 15
POLL_JOBS_SECONDS = 10
POLL_GPU_SECONDS = 30
POLL_ADMIN_OVERVIEW_SECONDS = 60

# Search debounce for the dashboard filter field.
SEARCH_DELAY_MS = 400


def poll_context() -> dict[str, int]:
    return {
        "poll_summary": POLL_SUMMARY_SECONDS,
        "poll_companies": POLL_COMPANIES_SECONDS,
        "poll_bootstrap": POLL_BOOTSTRAP_SECONDS,
        "poll_jobs": POLL_JOBS_SECONDS,
        "poll_gpu": POLL_GPU_SECONDS,
        "poll_admin_overview": POLL_ADMIN_OVERVIEW_SECONDS,
        "search_delay_ms": SEARCH_DELAY_MS,
    }
