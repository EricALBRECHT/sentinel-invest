"""Redis queues and deterministic job ids.

SEC retries twice after the first failure (three attempts). Score jobs retry
once (two attempts). A company keeps one active job id, so a second enqueue
while that job is queued or started is ignored.
"""

from redis import Redis
from rq import Queue, Retry
from rq.exceptions import NoSuchJobError
from rq.job import Job

from app.core.config import settings

QUEUE_DEFAULT = "default"
QUEUE_SEC = "sec"
QUEUE_ANALYSIS = "analysis"
QUEUE_MARKET = "market"
QUEUE_INTELLIGENCE = "intelligence"
QUEUE_GPU = "gpu"
QUEUE_NAMES = (QUEUE_DEFAULT, QUEUE_SEC, QUEUE_ANALYSIS, QUEUE_MARKET, QUEUE_INTELLIGENCE, QUEUE_GPU)

_ACTIVE = frozenset({"queued", "started", "deferred", "scheduled"})


def redis_connection() -> Redis:
    return Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        socket_keepalive=True,
        health_check_interval=30,
    )


def sec_retry() -> Retry:
    return Retry(max=2, interval=[60, 300])


def score_retry() -> Retry:
    return Retry(max=1, interval=[30])


def market_retry() -> Retry:
    return Retry(max=2, interval=[60, 300])


def sec_job_id(company_id: int) -> str:
    return f"sec-sync-company-{company_id}"


def quality_job_id(company_id: int) -> str:
    return f"quality-score-company-{company_id}"


def opportunity_job_id(company_id: int) -> str:
    return f"opportunity-score-company-{company_id}"


def market_job_id(company_id: int) -> str:
    return f"market-sync-company-{company_id}"


def technical_job_id(company_id: int) -> str:
    return f"technical-score-company-{company_id}"


def technical_backfill_job_id(company_id: int) -> str:
    return f"technical-backfill-company-{company_id}"


def news_job_id(company_id: int) -> str:
    return f"news-sync-company-{company_id}"


def source_poll_job_id(source_id: int) -> str:
    return f"external-source-poll-{source_id}"


def process_document_job_id(document_id: int) -> str:
    return f"intelligence-process-document-{document_id}"


def intelligence_retry() -> Retry:
    return Retry(max=2, interval=[60, 300])


def investment_view_job_id(company_id: int) -> str:
    return f"investment-view-company-{company_id}"


def enqueue_job(
    queue_name: str,
    func_path: str,
    company_id: int,
    *,
    job_id: str,
    retry: Retry,
    timeout: int,
) -> dict:
    return enqueue_call(
        queue_name,
        func_path,
        company_id,
        job_id=job_id,
        retry=retry,
        timeout=timeout,
    )


def enqueue_call(
    queue_name: str,
    func_path: str,
    *args,
    job_id: str,
    retry: Retry | None,
    timeout: int,
    replace_non_started: bool = False,
    meta: dict | None = None,
    **func_kwargs,
) -> dict:
    """Enqueue one job, or return it when that id is already queued or started.

    When replace_non_started is True (legacy fixed ids), any non-started job with
    the same id is deleted first so a finished/failed/scheduled retry cannot
    block a new execution or leave a stale result.
    """
    connection = redis_connection()
    existing = _existing_job(connection, job_id)
    if existing is not None:
        status = existing.get_status(refresh=True)
        if status == "started":
            return {
                "job_id": job_id,
                "queue": queue_name,
                "enqueued": False,
                "status": status,
            }
        if status in _ACTIVE and not replace_non_started:
            return {
                "job_id": job_id,
                "queue": queue_name,
                "enqueued": False,
                "status": status,
            }
        existing.delete()
    queue = Queue(queue_name, connection=connection)
    options: dict = {
        "job_id": job_id,
        "job_timeout": timeout,
        "result_ttl": 24 * 3600,
        "failure_ttl": 7 * 24 * 3600,
    }
    if retry is not None:
        options["retry"] = retry
    if meta:
        options["meta"] = meta
    if func_kwargs:
        job = queue.enqueue(func_path, args=args, kwargs=func_kwargs, **options)
    else:
        job = queue.enqueue(func_path, *args, **options)
    return {
        "job_id": job.id,
        "queue": queue_name,
        "enqueued": True,
        "status": job.get_status(),
    }


def enqueue_sec_sync(company_id: int) -> dict:
    return enqueue_job(
        QUEUE_SEC,
        "app.jobs.sec_sync.sync_company_sec",
        company_id,
        job_id=sec_job_id(company_id),
        retry=sec_retry(),
        timeout=900,
    )


def enqueue_quality(company_id: int) -> dict:
    return enqueue_job(
        QUEUE_ANALYSIS,
        "app.jobs.scoring.recalculate_quality",
        company_id,
        job_id=quality_job_id(company_id),
        retry=score_retry(),
        timeout=300,
    )


def enqueue_opportunity(company_id: int) -> dict:
    return enqueue_job(
        QUEUE_ANALYSIS,
        "app.jobs.scoring.recalculate_opportunity",
        company_id,
        job_id=opportunity_job_id(company_id),
        retry=score_retry(),
        timeout=300,
    )


def universe_priority_job_id() -> str:
    return "universe-priorities"


def enqueue_universe_priorities() -> dict:
    return enqueue_call(
        QUEUE_DEFAULT,
        "app.jobs.universe.recalculate_universe_priorities",
        job_id=universe_priority_job_id(),
        retry=score_retry(),
        timeout=300,
    )


def enqueue_market_sync(company_id: int) -> dict:
    return enqueue_job(
        QUEUE_MARKET,
        "app.jobs.market_sync.sync_company_market",
        company_id,
        job_id=market_job_id(company_id),
        retry=market_retry(),
        timeout=180,
    )


def enqueue_technical(company_id: int) -> dict:
    return enqueue_job(
        QUEUE_ANALYSIS,
        "app.jobs.technical.recalculate_technical",
        company_id,
        job_id=technical_job_id(company_id),
        retry=score_retry(),
        timeout=300,
    )


def enqueue_investment_view(company_id: int) -> dict:
    return enqueue_job(
        QUEUE_ANALYSIS,
        "app.jobs.investment.recalculate_investment",
        company_id,
        job_id=investment_view_job_id(company_id),
        retry=score_retry(),
        timeout=300,
    )


def enqueue_news_sync(company_id: int, force: bool = False) -> dict:
    return enqueue_call(
        QUEUE_INTELLIGENCE,
        "app.jobs.intelligence.sync_news_company",
        company_id,
        force,
        job_id=news_job_id(company_id),
        retry=intelligence_retry(),
        timeout=300,
    )


def enqueue_source_poll(source_id: int) -> dict:
    return enqueue_call(
        QUEUE_INTELLIGENCE,
        "app.jobs.intelligence.poll_external_source",
        source_id,
        job_id=source_poll_job_id(source_id),
        retry=intelligence_retry(),
        timeout=180,
    )


def supply_chain_job_id(company_id: int) -> str:
    return f"supply-chain-company-{company_id}"


def enqueue_supply_chain(company_id: int) -> dict:
    return enqueue_call(
        QUEUE_INTELLIGENCE,
        "app.jobs.supply_chain.process_supply_chain_company",
        company_id,
        job_id=supply_chain_job_id(company_id),
        retry=intelligence_retry(),
        timeout=300,
    )


def verify_candidate_job_id(candidate_id: int) -> str:
    return f"verify-candidate-{candidate_id}"


def discovery_verification_batch_job_id() -> str:
    return "verify-discovered-candidates"


def enqueue_verify_candidate(candidate_id: int) -> dict:
    return enqueue_call(
        QUEUE_INTELLIGENCE,
        "app.jobs.discovery_verification.verify_candidate_job",
        candidate_id,
        job_id=verify_candidate_job_id(candidate_id),
        retry=intelligence_retry(),
        timeout=180,
    )


def discovery_expansion_job_id(company_id: int) -> str:
    return f"expand-discovered-company-{company_id}"


def discovery_expansion_batch_job_id() -> str:
    return "expand-discovered-companies"


def universe_members_refresh_job_id() -> str:
    return "universe-members-refresh"


def universe_bootstrap_job_id() -> str:
    return "universe-bootstrap-data"


def enqueue_discovery_expansion(company_id: int) -> dict:
    return enqueue_call(
        QUEUE_INTELLIGENCE,
        "app.jobs.discovery_expansion.expand_discovered_company_job",
        company_id,
        job_id=discovery_expansion_job_id(company_id),
        retry=intelligence_retry(),
        timeout=300,
    )


def enqueue_discovery_expansion_batch() -> dict:
    return enqueue_call(
        QUEUE_INTELLIGENCE,
        "app.jobs.discovery_expansion.enqueue_due_discovery_expansions",
        job_id=discovery_expansion_batch_job_id(),
        retry=intelligence_retry(),
        timeout=180,
    )


def enqueue_universe_members_refresh() -> dict:
    return enqueue_call(
        QUEUE_DEFAULT,
        "app.jobs.universe.refresh_universe_members",
        job_id=universe_members_refresh_job_id(),
        retry=score_retry(),
        timeout=900,
    )


def enqueue_universe_bootstrap() -> dict:
    return enqueue_call(
        QUEUE_DEFAULT,
        "app.jobs.universe.bootstrap_universe_data_job",
        job_id=universe_bootstrap_job_id(),
        retry=score_retry(),
        timeout=300,
    )


def enqueue_discovery_verification_batch() -> dict:
    return enqueue_call(
        QUEUE_INTELLIGENCE,
        "app.jobs.discovery_verification.enqueue_due_discovery_verifications",
        job_id=discovery_verification_batch_job_id(),
        retry=intelligence_retry(),
        timeout=180,
    )


def ai_document_run_id() -> str:
    import uuid

    return uuid.uuid4().hex[:8]


def ai_document_job_id(document_id: int, run_id: str | None = None) -> str:
    """Unique per-run id. Legacy fixed ids (ai-document-{id}) are still recognized as active."""
    return f"ai-document-{document_id}-{run_id or ai_document_run_id()}"


def ai_document_gpu_job_id(document_id: int, run_id: str | None = None) -> str:
    return f"ai-gpu-document-{document_id}-{run_id or ai_document_run_id()}"


def ai_document_legacy_job_id(document_id: int) -> str:
    return f"ai-document-{document_id}"


def find_active_ai_document_job(document_id: int) -> Job | None:
    """Return a queued/started/deferred/scheduled job for this document, if any."""
    connection = redis_connection()
    legacy = _existing_job(connection, ai_document_legacy_job_id(document_id))
    if legacy is not None and legacy.get_status(refresh=True) in _ACTIVE:
        return legacy
    prefix = f"ai-document-{document_id}-"
    for job_id in _iter_active_job_ids(connection, QUEUE_INTELLIGENCE):
        if not (job_id == ai_document_legacy_job_id(document_id) or job_id.startswith(prefix)):
            continue
        job = _existing_job(connection, job_id)
        if job is None:
            continue
        if job.get_status(refresh=True) not in _ACTIVE:
            continue
        meta = job.meta or {}
        if meta.get("document_id") not in (None, document_id):
            continue
        return job
    return None


def enqueue_ai_document_analysis(document_id: int, force: bool = False) -> dict:
    active = find_active_ai_document_job(document_id)
    if active is not None:
        return {
            "job_id": active.id,
            "queue": QUEUE_INTELLIGENCE,
            "enqueued": False,
            "status": active.get_status(refresh=True),
        }
    run_id = ai_document_run_id()
    job_id = ai_document_job_id(document_id, run_id)
    # No automatic RQ retry: config/programming errors must not linger as scheduled.
    return enqueue_call(
        QUEUE_INTELLIGENCE,
        "app.jobs.ai_document.run_ai_document_analysis",
        document_id,
        job_id=job_id,
        retry=None,
        timeout=1200,
        meta={"document_id": document_id, "run_id": run_id, "force": bool(force)},
        force=force,
        run_id=run_id,
    )


def cleanup_stale_ai_document_job(job_id: str) -> dict:
    """Safely delete a non-running AI document job (finished/failed/scheduled retry leftover)."""
    connection = redis_connection()
    job = _existing_job(connection, job_id)
    if job is None:
        return {"job_id": job_id, "deleted": False, "reason": "not_found"}
    status = job.get_status(refresh=True)
    if status == "started":
        return {"job_id": job_id, "deleted": False, "reason": "started", "status": status}
    if status == "queued":
        return {"job_id": job_id, "deleted": False, "reason": "queued", "status": status}
    job.delete()
    return {"job_id": job_id, "deleted": True, "status": status}


def _iter_active_job_ids(connection: Redis, queue_name: str):
    from rq.registry import DeferredJobRegistry, ScheduledJobRegistry, StartedJobRegistry

    queue = Queue(queue_name, connection=connection)
    seen: set[str] = set()
    for job_id in queue.job_ids:
        if job_id not in seen:
            seen.add(job_id)
            yield job_id
    for registry_cls in (StartedJobRegistry, ScheduledJobRegistry, DeferredJobRegistry):
        for job_id in registry_cls(queue_name, connection=connection).get_job_ids():
            if job_id not in seen:
                seen.add(job_id)
                yield job_id


def gpu_probe_job_id() -> str:
    return "gpu-system-probe"


def enqueue_gpu_probe() -> dict:
    return enqueue_call(
        QUEUE_GPU,
        "app.jobs.gpu.probe.gpu_system_probe",
        job_id=gpu_probe_job_id(),
        retry=Retry(max=1, interval=[15]),
        timeout=60,
    )


def enqueue_process_document(document_id: int) -> dict:
    return enqueue_call(
        QUEUE_INTELLIGENCE,
        "app.jobs.intelligence.process_intelligence_document",
        document_id,
        job_id=process_document_job_id(document_id),
        retry=intelligence_retry(),
        timeout=120,
    )


def enqueue_technical_backfill(
    company_id: int,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    return enqueue_call(
        QUEUE_ANALYSIS,
        "app.jobs.technical.backfill_technical",
        company_id,
        start_date,
        end_date,
        job_id=technical_backfill_job_id(company_id),
        retry=score_retry(),
        timeout=900,
    )


def _active_job(connection: Redis, job_id: str) -> Job | None:
    job = _existing_job(connection, job_id)
    if job is None:
        return None
    if job.get_status(refresh=True) in _ACTIVE:
        return job
    return None


def _existing_job(connection: Redis, job_id: str) -> Job | None:
    try:
        return Job.fetch(job_id, connection=connection)
    except NoSuchJobError:
        return None


def _delete_finished(connection: Redis, job_id: str) -> None:
    job = _existing_job(connection, job_id)
    if job is None:
        return
    if job.get_status(refresh=True) not in _ACTIVE:
        job.delete()
