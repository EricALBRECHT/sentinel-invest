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
from app.models.investment_view import InvestmentView
from app.models.technical_snapshot import TechnicalSnapshot
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
    TechnicalCounts,
    InvestmentViewCounts,
    IntelligenceCounts,
    SupplyChainCounts,
    DiscoveryVerificationCounts,
    DiscoveryExpansionCounts,
)
from app.services.intelligence.documents import intelligence_counts
from app.services.supply_chain.extraction import supply_chain_counts
from app.services.discovery_verification.verifier import verification_counts
from app.services.discovery_expansion.summary import expansion_counts
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
_EMPTY_TECHNICAL = TechnicalCounts(
    companies_scored=None,
    companies_without_score=None,
    last_calculation=None,
    technical_snapshots_count=None,
    oldest_technical_date=None,
    latest_technical_date=None,
)
_EMPTY_INVESTMENT = InvestmentViewCounts(companies_with_view=None, last_calculation=None)
_EMPTY_SUPPLY = SupplyChainCounts(
    relationships_total=None,
    confirmed_relationships=None,
    candidate_relationships=None,
    discovered_companies=None,
    last_processing=None,
)
_EMPTY_DISCOVERY = DiscoveryVerificationCounts(
    unverified=None,
    partial=None,
    verified=None,
    promoted=None,
    rejected=None,
    last_verification=None,
)
_EMPTY_EXPANSION = DiscoveryExpansionCounts(
    ready=None,
    collecting=None,
    analyzed=None,
    blocked=None,
    max_depth=None,
    deepest_company=None,
    last_expansion=None,
)
_EMPTY_INTELLIGENCE = IntelligenceCounts(
    sources_active=None,
    documents_total=None,
    documents_last_24h=None,
    events_total=None,
    events_last_24h=None,
    last_success=None,
    failed_sources=None,
)


async def build_admin_status(db: AsyncSession) -> AdminStatusRead:
    postgres = await _postgres_status(db)
    redis_status = await _redis_status()
    if postgres == "ok":
        data, analysis, sec = await _counts(db)
        sync = await _sync_counts(db)
        universe = await _universe_counts(db)
        market = await _market_counts(db)
        technical = await _technical_counts(db)
        investment = await _investment_counts(db)
        intelligence = await _intelligence_counts(db)
        supply = await _supply_chain_counts(db)
        discovery = await _discovery_counts(db)
        expansion = await _expansion_counts(db)
    else:
        data, analysis, sec, sync = _EMPTY_DATA, _EMPTY_ANALYSIS, _EMPTY_SEC, _EMPTY_SYNC
        universe = _EMPTY_UNIVERSE
        market = _EMPTY_MARKET
        technical = _EMPTY_TECHNICAL
        investment = _EMPTY_INVESTMENT
        intelligence = _EMPTY_INTELLIGENCE
        supply = _EMPTY_SUPPLY
        discovery = _EMPTY_DISCOVERY
        expansion = _EMPTY_EXPANSION
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
        technical=technical,
        investment_view=investment,
        intelligence=intelligence,
        supply_chain=supply,
        discovery_verification=discovery,
        discovery_expansion=expansion,
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


async def _technical_counts(db: AsyncSession) -> TechnicalCounts:
    try:
        scored = int(
            (
                await db.execute(
                    select(func.count(func.distinct(TechnicalSnapshot.company_id))).where(
                        TechnicalSnapshot.technical_score.is_not(None)
                    )
                )
            ).scalar_one()
        )
        total = await _count(db, Company)
        last_calculation = await db.scalar(select(func.max(TechnicalSnapshot.updated_at)))
        snapshots = await _count(db, TechnicalSnapshot)
        oldest = await db.scalar(select(func.min(TechnicalSnapshot.as_of_date)))
        latest = await db.scalar(select(func.max(TechnicalSnapshot.as_of_date)))
    except Exception as exc:
        logger.warning("admin_status_technical_count_failed error_type=%s", type(exc).__name__)
        return _EMPTY_TECHNICAL
    return TechnicalCounts(
        companies_scored=scored,
        companies_without_score=max(0, total - scored),
        last_calculation=last_calculation,
        technical_snapshots_count=snapshots,
        oldest_technical_date=oldest,
        latest_technical_date=latest,
    )


async def _investment_counts(db: AsyncSession) -> InvestmentViewCounts:
    try:
        companies = int(
            (
                await db.execute(select(func.count(func.distinct(InvestmentView.company_id))))
            ).scalar_one()
        )
        last_calculation = await db.scalar(select(func.max(InvestmentView.updated_at)))
    except Exception as exc:
        logger.warning("admin_status_investment_count_failed error_type=%s", type(exc).__name__)
        return _EMPTY_INVESTMENT
    return InvestmentViewCounts(companies_with_view=companies, last_calculation=last_calculation)


async def _intelligence_counts(db: AsyncSession) -> IntelligenceCounts:
    try:
        counted = await intelligence_counts(db)
    except Exception as exc:
        logger.warning("admin_status_intelligence_count_failed error_type=%s", type(exc).__name__)
        return _EMPTY_INTELLIGENCE
    return IntelligenceCounts(**counted)


async def _supply_chain_counts(db: AsyncSession) -> SupplyChainCounts:
    try:
        counted = await supply_chain_counts(db)
    except Exception as exc:
        logger.warning("admin_status_supply_chain_count_failed error_type=%s", type(exc).__name__)
        return _EMPTY_SUPPLY
    return SupplyChainCounts(**counted)


async def _expansion_counts(db: AsyncSession) -> DiscoveryExpansionCounts:
    try:
        counted = await expansion_counts(db)
    except Exception as exc:
        logger.warning("admin_status_discovery_expansion_failed error_type=%s", type(exc).__name__)
        return _EMPTY_EXPANSION
    return DiscoveryExpansionCounts(**counted)


async def _discovery_counts(db: AsyncSession) -> DiscoveryVerificationCounts:
    try:
        counted = await verification_counts(db)
    except Exception as exc:
        logger.warning("admin_status_discovery_verification_failed error_type=%s", type(exc).__name__)
        return _EMPTY_DISCOVERY
    return DiscoveryVerificationCounts(**counted)


async def _count(db: AsyncSession, model, *criteria) -> int:
    statement = select(func.count()).select_from(model)
    for criterion in criteria:
        statement = statement.where(criterion)
    return int((await db.execute(statement)).scalar_one())
