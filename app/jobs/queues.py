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
QUEUE_NAMES = (QUEUE_DEFAULT, QUEUE_SEC, QUEUE_ANALYSIS, QUEUE_MARKET, QUEUE_INTELLIGENCE)

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
    retry: Retry,
    timeout: int,
) -> dict:
    """Enqueue one job, or return it when that id is already queued or started."""
    connection = redis_connection()
    active = _active_job(connection, job_id)
    if active is not None:
        return {
            "job_id": job_id,
            "queue": queue_name,
            "enqueued": False,
            "status": active.get_status(),
        }
    _delete_finished(connection, job_id)
    queue = Queue(queue_name, connection=connection)
    job = queue.enqueue(
        func_path,
        *args,
        job_id=job_id,
        retry=retry,
        job_timeout=timeout,
        result_ttl=24 * 3600,
        failure_ttl=7 * 24 * 3600,
    )
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


def enqueue_discovery_verification_batch() -> dict:
    return enqueue_call(
        QUEUE_INTELLIGENCE,
        "app.jobs.discovery_verification.enqueue_due_discovery_verifications",
        job_id=discovery_verification_batch_job_id(),
        retry=intelligence_retry(),
        timeout=180,
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
    try:
        job = Job.fetch(job_id, connection=connection)
    except NoSuchJobError:
        return None
    if job.get_status(refresh=True) in _ACTIVE:
        return job
    return None


def _delete_finished(connection: Redis, job_id: str) -> None:
    try:
        job = Job.fetch(job_id, connection=connection)
    except NoSuchJobError:
        return
    if job.get_status(refresh=True) not in _ACTIVE:
        job.delete()
