"""Progressive market/SEC bootstrap for screened index members."""

from datetime import datetime, timezone
import logging

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.config import settings
from app.jobs.queues import enqueue_market_sync, enqueue_sec_sync, redis_connection
from app.models.company import Company
from app.models.company_market_snapshot import CompanyMarketSnapshot
from app.models.company_score import CompanyScore
from app.models.company_sync_status import CompanySyncStatus
from app.models.investment_view import InvestmentView
from app.models.technical_snapshot import TechnicalSnapshot
from app.models.universe_membership import UniverseMembership
from app.services.market.provider import MARKET_SOURCE
from app.services.sec.mappings import SEC_SOURCE

logger = logging.getLogger("sentinel.universe")
BOOTSTRAP_KEY = "sentinel:universe:bootstrap:last"
_INDEX_SOURCES = ("SP500", "NASDAQ100")


async def bootstrap_universe_data(
    session: AsyncSession,
    *,
    limit: int | None = None,
) -> dict:
    batch = settings.universe_bootstrap_max_per_run if limit is None else max(0, limit)
    companies = await select_bootstrap_companies(session, limit=batch)
    selected = 0
    market_enqueued = 0
    sec_enqueued = 0
    already_active = 0
    job_ids: list[str] = []
    for company in companies:
        selected += 1
        if not await _has_market(session, company.id):
            queued = enqueue_market_sync(company.id)
            if queued["enqueued"]:
                market_enqueued += 1
                job_ids.append(queued["job_id"])
            else:
                already_active += 1
        if company.sec_cik and not await _has_sec(session, company.id):
            queued = enqueue_sec_sync(company.id)
            if queued["enqueued"]:
                sec_enqueued += 1
                job_ids.append(queued["job_id"])
            else:
                already_active += 1
    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    try:
        redis_connection().set(BOOTSTRAP_KEY, stamp)
    except Exception as exc:
        logger.warning("universe_bootstrap_stamp_failed error_type=%s", type(exc).__name__)
    result = {
        "selected": selected,
        "market_enqueued": market_enqueued,
        "sec_enqueued": sec_enqueued,
        "already_active": already_active,
        "job_ids": job_ids,
        "limit": batch,
        "last_bootstrap": stamp,
    }
    logger.info(
        "universe_bootstrap_finished selected=%s market=%s sec=%s already_active=%s",
        selected,
        market_enqueued,
        sec_enqueued,
        already_active,
    )
    return result


async def select_bootstrap_companies(session: AsyncSession, *, limit: int) -> list[Company]:
    if limit <= 0:
        return []
    market = aliased(CompanySyncStatus)
    sec = aliased(CompanySyncStatus)
    membership = select(UniverseMembership.id).where(
        UniverseMembership.company_id == Company.id,
        UniverseMembership.is_active.is_(True),
        UniverseMembership.source.in_(_INDEX_SOURCES),
    )
    needs_market = or_(market.id.is_(None), market.last_success_at.is_(None))
    needs_sec = and_(Company.sec_cik.is_not(None), or_(sec.id.is_(None), sec.last_success_at.is_(None)))
    statement = (
        select(Company)
        .outerjoin(market, and_(market.company_id == Company.id, market.source == MARKET_SOURCE))
        .outerjoin(sec, and_(sec.company_id == Company.id, sec.source == SEC_SOURCE))
        .where(
            Company.is_active.is_(True),
            membership.exists(),
            or_(needs_market, needs_sec),
        )
        .order_by(
            Company.universe_priority.desc(),
            needs_market.desc(),
            needs_sec.desc(),
            Company.id.asc(),
        )
        .limit(limit)
    )
    return list((await session.execute(statement)).scalars().all())


async def universe_market_status(session: AsyncSession) -> dict:
    from app.services.universe.refresh import last_refresh

    members_sp500 = await _active_members(session, "SP500")
    members_nasdaq = await _active_members(session, "NASDAQ100")
    pending_market = await _pending_market_count(session)
    pending_sec = await _pending_sec_count(session)
    ready = await _ready_count(session)
    last_bootstrap = _last_bootstrap()
    return {
        "SP500": {"members": members_sp500, "last_refresh": last_refresh("SP500")},
        "NASDAQ100": {"members": members_nasdaq, "last_refresh": last_refresh("NASDAQ100")},
        "bootstrap": {
            "pending_market": pending_market,
            "pending_sec": pending_sec,
            "ready": ready,
            "last_bootstrap": last_bootstrap,
            "max_per_run": settings.universe_bootstrap_max_per_run,
        },
    }


async def completeness_for(session: AsyncSession, company_ids: list[int]) -> dict[int, dict[str, bool]]:
    if not company_ids:
        return {}
    market_ids = set(
        (
            await session.execute(
                select(CompanyMarketSnapshot.company_id).where(CompanyMarketSnapshot.company_id.in_(company_ids))
            )
        ).scalars()
    )
    sec_ids = set(
        (
            await session.execute(
                select(CompanySyncStatus.company_id).where(
                    CompanySyncStatus.company_id.in_(company_ids),
                    CompanySyncStatus.source == SEC_SOURCE,
                    CompanySyncStatus.last_success_at.is_not(None),
                )
            )
        ).scalars()
    )
    quality_ids = set(
        (await session.execute(select(CompanyScore.company_id).where(CompanyScore.company_id.in_(company_ids)))).scalars()
    )
    technical_ids = set(
        (
            await session.execute(
                select(TechnicalSnapshot.company_id).where(TechnicalSnapshot.company_id.in_(company_ids))
            )
        ).scalars()
    )
    view_ids = set(
        (
            await session.execute(select(InvestmentView.company_id).where(InvestmentView.company_id.in_(company_ids)))
        ).scalars()
    )
    return {
        company_id: {
            "market_ready": company_id in market_ids,
            "sec_ready": company_id in sec_ids,
            "quality_ready": company_id in quality_ids,
            "technical_ready": company_id in technical_ids,
            "investment_view_ready": company_id in view_ids,
        }
        for company_id in company_ids
    }


async def _active_members(session: AsyncSession, universe_name: str) -> int:
    statement = select(func.count()).select_from(UniverseMembership).where(
        UniverseMembership.universe_name == universe_name,
        UniverseMembership.is_active.is_(True),
    )
    return int((await session.execute(statement)).scalar_one())


async def _pending_market_count(session: AsyncSession) -> int:
    market = aliased(CompanySyncStatus)
    membership = select(UniverseMembership.id).where(
        UniverseMembership.company_id == Company.id,
        UniverseMembership.is_active.is_(True),
        UniverseMembership.source.in_(_INDEX_SOURCES),
    )
    statement = (
        select(func.count())
        .select_from(Company)
        .outerjoin(market, and_(market.company_id == Company.id, market.source == MARKET_SOURCE))
        .where(
            Company.is_active.is_(True),
            membership.exists(),
            or_(market.id.is_(None), market.last_success_at.is_(None)),
        )
    )
    return int((await session.execute(statement)).scalar_one())


async def _pending_sec_count(session: AsyncSession) -> int:
    sec = aliased(CompanySyncStatus)
    membership = select(UniverseMembership.id).where(
        UniverseMembership.company_id == Company.id,
        UniverseMembership.is_active.is_(True),
        UniverseMembership.source.in_(_INDEX_SOURCES),
    )
    statement = (
        select(func.count())
        .select_from(Company)
        .outerjoin(sec, and_(sec.company_id == Company.id, sec.source == SEC_SOURCE))
        .where(
            Company.is_active.is_(True),
            Company.sec_cik.is_not(None),
            membership.exists(),
            or_(sec.id.is_(None), sec.last_success_at.is_(None)),
        )
    )
    return int((await session.execute(statement)).scalar_one())


async def _ready_count(session: AsyncSession) -> int:
    market = aliased(CompanySyncStatus)
    membership = select(UniverseMembership.id).where(
        UniverseMembership.company_id == Company.id,
        UniverseMembership.is_active.is_(True),
        UniverseMembership.source.in_(_INDEX_SOURCES),
    )
    statement = (
        select(func.count())
        .select_from(Company)
        .join(market, and_(market.company_id == Company.id, market.source == MARKET_SOURCE))
        .where(
            Company.is_active.is_(True),
            membership.exists(),
            market.last_success_at.is_not(None),
            or_(
                Company.sec_cik.is_(None),
                select(CompanySyncStatus.id)
                .where(
                    CompanySyncStatus.company_id == Company.id,
                    CompanySyncStatus.source == SEC_SOURCE,
                    CompanySyncStatus.last_success_at.is_not(None),
                )
                .exists(),
            ),
        )
    )
    return int((await session.execute(statement)).scalar_one())


async def _has_market(session: AsyncSession, company_id: int) -> bool:
    row = await session.scalar(
        select(CompanySyncStatus.id).where(
            CompanySyncStatus.company_id == company_id,
            CompanySyncStatus.source == MARKET_SOURCE,
            CompanySyncStatus.last_success_at.is_not(None),
        )
    )
    return row is not None


async def _has_sec(session: AsyncSession, company_id: int) -> bool:
    row = await session.scalar(
        select(CompanySyncStatus.id).where(
            CompanySyncStatus.company_id == company_id,
            CompanySyncStatus.source == SEC_SOURCE,
            CompanySyncStatus.last_success_at.is_not(None),
        )
    )
    return row is not None


def _last_bootstrap() -> str | None:
    try:
        raw = redis_connection().get(BOOTSTRAP_KEY)
    except Exception:
        return None
    if raw is None:
        return None
    if isinstance(raw, bytes):
        return raw.decode()
    return str(raw)
