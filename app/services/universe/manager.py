"""Add, remove, list, and archive universe members."""

from datetime import datetime, timedelta, timezone
import logging

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.company import Company
from app.models.company_score import CompanyScore
from app.models.opportunity_score import OpportunityScore
from app.models.universe_membership import UniverseMembership
from app.services.universe.ranking import PriorityInputs, priority_parts
from app.services.universe.rules import (
    UniverseRuleError,
    normalize_source,
    normalize_universe_name,
    status_after_add,
)

logger = logging.getLogger("sentinel.universe")


class CompanyNotFound(Exception):
    """The requested company does not exist."""


class MembershipNotFound(Exception):
    """No active membership matches the request."""


async def add_company_to_universe(
    session: AsyncSession,
    company_id: int,
    universe_name: str,
    source: str,
    *,
    universe_status: str | None = None,
    discovery_reason: str | None = None,
    metadata_json: dict | None = None,
    commit: bool = True,
) -> tuple[UniverseMembership, bool]:
    name = normalize_universe_name(universe_name)
    origin = normalize_source(source)
    company = await _company(session, company_id)
    existing = await _active_membership(session, company.id, name, origin)
    now = datetime.now(timezone.utc)
    if existing is not None:
        _touch(company, now, origin, universe_status, discovery_reason)
        if commit:
            await session.commit()
        return existing, False

    membership = UniverseMembership(
        company_id=company.id,
        universe_name=name,
        source=origin,
        added_at=now,
        is_active=True,
        metadata_json=metadata_json,
    )
    session.add(membership)
    _touch(company, now, origin, universe_status, discovery_reason)
    if company.first_seen_at is None:
        company.first_seen_at = now
    if commit:
        await session.commit()
    else:
        await session.flush()
    logger.info(
        "universe_member_added company_id=%s universe=%s source=%s",
        company.id,
        name,
        origin,
    )
    return membership, True


async def remove_company_from_universe(
    session: AsyncSession,
    company_id: int,
    universe_name: str,
    source: str,
) -> UniverseMembership:
    name = normalize_universe_name(universe_name)
    origin = normalize_source(source)
    company = await _company(session, company_id)
    membership = await _active_membership(session, company.id, name, origin)
    if membership is None:
        raise MembershipNotFound
    membership.is_active = False
    membership.removed_at = datetime.now(timezone.utc)
    await session.commit()
    logger.info(
        "universe_member_removed company_id=%s universe=%s source=%s",
        company.id,
        name,
        origin,
    )
    return membership


async def list_universe(
    session: AsyncSession,
    *,
    universe_name: str | None = None,
    status: str | None = None,
    source: str | None = None,
    active: bool | None = None,
    search: str | None = None,
    statuses: list[str] | None = None,
    require_membership: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Company]:
    statement = _universe_statement(
        universe_name=universe_name,
        status=status,
        source=source,
        active=active,
        search=search,
        statuses=statuses,
        require_membership=require_membership,
    )
    statement = statement.order_by(Company.universe_priority.desc(), Company.id.asc())
    statement = statement.offset(offset).limit(limit)
    return list((await session.execute(statement)).scalars().all())


async def count_universe(
    session: AsyncSession,
    *,
    universe_name: str | None = None,
    status: str | None = None,
    source: str | None = None,
    active: bool | None = None,
    search: str | None = None,
    statuses: list[str] | None = None,
    require_membership: bool | None = None,
) -> int:
    statement = select(func.count()).select_from(
        _universe_statement(
            universe_name=universe_name,
            status=status,
            source=source,
            active=active,
            search=search,
            statuses=statuses,
            require_membership=require_membership,
        ).subquery()
    )
    return int((await session.execute(statement)).scalar_one())


def _universe_statement(
    *,
    universe_name: str | None = None,
    status: str | None = None,
    source: str | None = None,
    active: bool | None = None,
    search: str | None = None,
    statuses: list[str] | None = None,
    require_membership: bool | None = None,
):
    name = normalize_universe_name(universe_name) if universe_name else None
    origin = normalize_source(source) if source else None
    if status:
        from app.services.universe.rules import normalize_status

        status = normalize_status(status)
    normalized_statuses = None
    if statuses:
        from app.services.universe.rules import normalize_status

        normalized_statuses = [normalize_status(item) for item in statuses]
    statement = select(Company)
    must_join = require_membership
    if must_join is None:
        must_join = active is not False or name is not None or origin is not None
    if must_join:
        membership = select(UniverseMembership.id).where(UniverseMembership.company_id == Company.id)
        if active is not False:
            membership = membership.where(UniverseMembership.is_active.is_(True))
        if name:
            membership = membership.where(UniverseMembership.universe_name == name)
        if origin:
            membership = membership.where(UniverseMembership.source == origin)
        statement = statement.where(membership.exists())
    if status:
        statement = statement.where(Company.universe_status == status)
    if normalized_statuses:
        statement = statement.where(Company.universe_status.in_(normalized_statuses))
    if active is not None:
        statement = statement.where(Company.is_active.is_(active))
    needle = (search or "").strip()
    if needle:
        pattern = f"%{needle}%"
        statement = statement.where(or_(Company.ticker.ilike(pattern), Company.name.ilike(pattern)))
    return statement


async def get_company_universes(
    session: AsyncSession,
    company_id: int,
    *,
    active: bool | None = None,
) -> list[UniverseMembership]:
    await _company(session, company_id)
    statement = select(UniverseMembership).where(UniverseMembership.company_id == company_id)
    if active is not None:
        statement = statement.where(UniverseMembership.is_active.is_(active))
    statement = statement.order_by(UniverseMembership.added_at.asc(), UniverseMembership.id.asc())
    return list((await session.execute(statement)).scalars().all())


async def recalculate_universe_priority(session: AsyncSession, company: Company) -> dict[str, int]:
    parts = priority_parts(await _inputs(session, company))
    company.universe_priority = parts["total"]
    return parts


async def recalculate_universe_priorities(session: AsyncSession) -> dict[str, int]:
    companies = list(
        (await session.execute(select(Company).where(Company.is_active.is_(True)).order_by(Company.id))).scalars()
    )
    changed = 0
    for company in companies:
        previous = company.universe_priority
        await recalculate_universe_priority(session, company)
        if company.universe_priority != previous:
            changed += 1
    await session.commit()
    logger.info("universe_priorities_recalculated companies=%s changed=%s", len(companies), changed)
    return {"companies": len(companies), "changed": changed}


async def archive_stale_companies(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    stale_days: int | None = None,
) -> list[int]:
    moment = now or datetime.now(timezone.utc)
    days = settings.universe_stale_days if stale_days is None else stale_days
    cutoff = moment - timedelta(days=days)
    statement = select(Company).where(
        Company.is_active.is_(True),
        Company.universe_status != "ARCHIVED",
        (
            (Company.last_seen_at.is_not(None) & (Company.last_seen_at < cutoff))
            | (Company.last_seen_at.is_(None) & (Company.first_seen_at.is_(None) | (Company.first_seen_at < cutoff)))
        ),
    )
    companies = list((await session.execute(statement)).scalars())
    archived: list[int] = []
    for company in companies:
        company.universe_status = "ARCHIVED"
        company.is_active = False
        company.universe_priority = 0
        memberships = await get_company_universes(session, company.id, active=True)
        for membership in memberships:
            membership.is_active = False
            membership.removed_at = moment
        archived.append(company.id)
    await session.commit()
    logger.info("universe_companies_archived count=%s", len(archived))
    return archived


async def active_universe_count(session: AsyncSession, company_id: int) -> int:
    statement = select(func.count(func.distinct(UniverseMembership.universe_name))).where(
        UniverseMembership.company_id == company_id,
        UniverseMembership.is_active.is_(True),
    )
    return int((await session.execute(statement)).scalar_one())


async def _inputs(session: AsyncSession, company: Company) -> PriorityInputs:
    quality = await session.scalar(
        select(CompanyScore.quality_score)
        .where(CompanyScore.company_id == company.id)
        .order_by(CompanyScore.score_date.desc(), CompanyScore.id.desc())
        .limit(1)
    )
    opportunity = (
        await session.execute(
            select(OpportunityScore.opportunity_score, OpportunityScore.ranking_eligible)
            .where(OpportunityScore.company_id == company.id)
            .order_by(OpportunityScore.score_date.desc(), OpportunityScore.id.desc())
            .limit(1)
        )
    ).one_or_none()
    return PriorityInputs(
        universe_status=company.universe_status,
        quality_score=quality,
        opportunity_score=None if opportunity is None else opportunity[0],
        ranking_eligible=False if opportunity is None else bool(opportunity[1]),
        active_universe_count=await active_universe_count(session, company.id),
        discovery_source=company.discovery_source,
        pea_eligible=company.pea_eligible,
    )


async def _company(session: AsyncSession, company_id: int) -> Company:
    company = await session.get(Company, company_id)
    if company is None:
        raise CompanyNotFound
    return company


async def _active_membership(
    session: AsyncSession,
    company_id: int,
    universe_name: str,
    source: str,
) -> UniverseMembership | None:
    statement = select(UniverseMembership).where(
        UniverseMembership.company_id == company_id,
        UniverseMembership.universe_name == universe_name,
        UniverseMembership.source == source,
        UniverseMembership.is_active.is_(True),
    )
    return (await session.execute(statement)).scalar_one_or_none()


def _touch(
    company: Company,
    now: datetime,
    source: str,
    universe_status: str | None,
    discovery_reason: str | None,
) -> None:
    company.last_seen_at = now
    company.is_active = True
    company.universe_status = status_after_add(company.universe_status, universe_status)
    if company.discovery_source is None:
        company.discovery_source = source
    if discovery_reason:
        reason = discovery_reason.strip()
        if len(reason) > 500:
            raise UniverseRuleError("Discovery reason is too long")
        company.discovery_reason = reason
