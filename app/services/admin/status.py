"""Live supervision snapshot. Checks are real, and failures stay in the payload.

The response never includes connection strings, passwords, or other secrets.
A failed check is logged with the exception type only.
"""

from datetime import datetime, timezone
import asyncio
import logging

from redis.asyncio import Redis
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.company import Company
from app.models.company_score import CompanyScore
from app.models.company_sync_status import CompanySyncStatus
from app.models.financial_metric import FinancialMetric
from app.models.market_price import MarketPrice
from app.models.universe_membership import UniverseMembership
from app.models.opportunity_score import OpportunityScore
from app.models.user import User
from app.runtime import STARTED_AT
from app.schemas.admin import (
    AdminStatusRead,
    AnalysisCounts,
    DataCounts,
    JobQueueStatus,
    SecCounts,
    ServerStatus,
    SyncSupervision,
    SystemStatus,
    UniverseCounts,
    MarketCounts,
)
from app.services.jobs.schedule import select_due_company_ids
from app.services.jobs.status import collect_job_counts
from app.services.market.provider import MARKET_SOURCE
from app.services.market.schedule import select_due_market_company_ids
from app.services.sec.mappings import SEC_SOURCE
from app.services.universe.rules import CANONICAL_UNIVERSES

logger = logging.getLogger("sentinel.admin")

_EMPTY_DATA = DataCounts(
    companies=None,
    financial_metrics=None,
    quality_scores=None,
    opportunity_scores=None,
    users=None,
)
_EMPTY_ANALYSIS = AnalysisCounts(
    opportunity_incomplete=None,
    opportunity_partial=None,
    opportunity_usable=None,
    opportunity_complete=None,
    ranking_eligible=None,
)
_EMPTY_SEC = SecCounts(companies_with_cik=None, companies_without_cik=None)
_EMPTY_JOBS = JobQueueStatus(queued=None, started=None, finished_recent=None, failed=None)
_EMPTY_SYNC = SyncSupervision(sec_due=None, sec_last_success=None, sec_failures=None)
_EMPTY_UNIVERSE = UniverseCounts(
    active_companies=None,
    discovered=None,
    watched=None,
    screened=None,
    deep_analysis=None,
    portfolio=None,
    archived=None,
    universes=None,
)
_EMPTY_MARKET = MarketCounts(
    companies_with_market_data=None,
    companies_without_market_data=None,
    last_market_sync=None,
    market_jobs_due=None,
)


async def build_admin_status(db: AsyncSession) -> AdminStatusRead:
    postgres = await _postgres_status(db)
    redis_status = await _redis_status()
    if postgres == "ok":
        data, analysis, sec = await _counts(db)
        sync = await _sync_counts(db)
        universe = await _universe_counts(db)
        market = await _market_counts(db)
    else:
        data, analysis, sec, sync = _EMPTY_DATA, _EMPTY_ANALYSIS, _EMPTY_SEC, _EMPTY_SYNC
        universe = _EMPTY_UNIVERSE
        market = _EMPTY_MARKET
    jobs = _EMPTY_JOBS
    if redis_status == "ok":
        counted = await asyncio.to_thread(collect_job_counts)
        if counted is not None:
            jobs = JobQueueStatus(**counted)
    now = datetime.now(timezone.utc)
    return AdminStatusRead(
        system=SystemStatus(api="ok", postgres=postgres, redis=redis_status),
        data=data,
        analysis=analysis,
        sec=sec,
        jobs=jobs,
        sync=sync,
        universe=universe,
        market=market,
        server=ServerStatus(
            started_at=STARTED_AT,
            uptime_seconds=max(0, int((now - STARTED_AT).total_seconds())),
        ),
    )


async def _postgres_status(db: AsyncSession) -> str:
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("admin_status_postgres_check_failed error_type=%s", type(exc).__name__)
        return "error"
    return "ok"


async def _redis_status() -> str:
    client = Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        decode_responses=True,
    )
    try:
        if not await client.ping():
            logger.warning("admin_status_redis_check_failed error_type=PingReturnedFalse")
            return "error"
    except Exception as exc:
        logger.warning("admin_status_redis_check_failed error_type=%s", type(exc).__name__)
        return "error"
    else:
        return "ok"
    finally:
        await client.aclose()


async def _counts(db: AsyncSession) -> tuple[DataCounts, AnalysisCounts, SecCounts]:
    try:
        statuses = dict(
            (await db.execute(
                select(OpportunityScore.coverage_status, func.count()).group_by(OpportunityScore.coverage_status)
            )).all()
        )
        data = DataCounts(
            companies=await _count(db, Company),
            financial_metrics=await _count(db, FinancialMetric),
            quality_scores=await _count(db, CompanyScore),
            opportunity_scores=await _count(db, OpportunityScore),
            users=await _count(db, User),
        )
        analysis = AnalysisCounts(
            opportunity_incomplete=int(statuses.get("INCOMPLETE", 0)),
            opportunity_partial=int(statuses.get("PARTIAL", 0)),
            opportunity_usable=int(statuses.get("USABLE", 0)),
            opportunity_complete=int(statuses.get("COMPLETE", 0)),
            ranking_eligible=await _count(db, OpportunityScore, OpportunityScore.ranking_eligible.is_(True)),
        )
        sec = SecCounts(
            companies_with_cik=await _count(db, Company, Company.sec_cik.is_not(None)),
            companies_without_cik=await _count(db, Company, Company.sec_cik.is_(None)),
        )
    except Exception as exc:
        logger.warning("admin_status_count_failed error_type=%s", type(exc).__name__)
        return _EMPTY_DATA, _EMPTY_ANALYSIS, _EMPTY_SEC
    return data, analysis, sec


async def _sync_counts(db: AsyncSession) -> SyncSupervision:
    try:
        due = await select_due_company_ids(db)
        last_success = await db.scalar(
            select(func.max(CompanySyncStatus.last_success_at)).where(CompanySyncStatus.source == SEC_SOURCE)
        )
        failures = await _count(
            db,
            CompanySyncStatus,
            CompanySyncStatus.source == SEC_SOURCE,
            CompanySyncStatus.consecutive_failures > 0,
        )
    except Exception as exc:
        logger.warning("admin_status_sync_count_failed error_type=%s", type(exc).__name__)
        return _EMPTY_SYNC
    return SyncSupervision(sec_due=len(due), sec_last_success=last_success, sec_failures=failures)


async def _universe_counts(db: AsyncSession) -> UniverseCounts:
    try:
        statuses = dict(
            (await db.execute(select(Company.universe_status, func.count()).group_by(Company.universe_status))).all()
        )
        names = dict(
            (
                await db.execute(
                    select(UniverseMembership.universe_name, func.count())
                    .where(UniverseMembership.is_active.is_(True))
                    .group_by(UniverseMembership.universe_name)
                )
            ).all()
        )
        universes = {name: int(names.get(name, 0)) for name in CANONICAL_UNIVERSES}
        for name, count in names.items():
            universes[str(name)] = int(count)
        active_companies = await _count(db, Company, Company.is_active.is_(True))
    except Exception as exc:
        logger.warning("admin_status_universe_count_failed error_type=%s", type(exc).__name__)
        return _EMPTY_UNIVERSE
    return UniverseCounts(
        active_companies=active_companies,
        discovered=int(statuses.get("DISCOVERED", 0)),
        watched=int(statuses.get("WATCHED", 0)),
        screened=int(statuses.get("SCREENED", 0)),
        deep_analysis=int(statuses.get("DEEP_ANALYSIS", 0)),
        portfolio=int(statuses.get("PORTFOLIO", 0)),
        archived=int(statuses.get("ARCHIVED", 0)),
        universes=universes,
    )


async def _market_counts(db: AsyncSession) -> MarketCounts:
    try:
        with_data = int(
            (
                await db.execute(select(func.count(func.distinct(MarketPrice.company_id))))
            ).scalar_one()
        )
        total = await _count(db, Company)
        last_sync = await db.scalar(
            select(func.max(CompanySyncStatus.last_success_at)).where(CompanySyncStatus.source == MARKET_SOURCE)
        )
        due = await select_due_market_company_ids(db)
    except Exception as exc:
        logger.warning("admin_status_market_count_failed error_type=%s", type(exc).__name__)
        return _EMPTY_MARKET
    return MarketCounts(
        companies_with_market_data=with_data,
        companies_without_market_data=max(0, total - with_data),
        last_market_sync=last_sync,
        market_jobs_due=len(due),
    )


async def _count(db: AsyncSession, model, *criteria) -> int:
    statement = select(func.count()).select_from(model)
    for criterion in criteria:
        statement = statement.where(criterion)
    return int((await db.execute(statement)).scalar_one())
