"""Apply an index provider to Company + UniverseMembership rows."""

from datetime import datetime, timezone
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.queues import redis_connection
from app.models.company import Company
from app.models.universe_membership import UniverseMembership
from app.services.universe.manager import add_company_to_universe, recalculate_universe_priority
from app.services.universe.providers import UniverseProvider, provider_for
from app.services.universe.providers.base import NormalizedMember

logger = logging.getLogger("sentinel.universe")
REFRESH_KEY = "sentinel:universe:refresh:{universe}"


async def refresh_universe(
    session: AsyncSession,
    universe_name: str,
    *,
    provider: UniverseProvider | None = None,
) -> dict:
    selected = provider or provider_for(universe_name)
    members = await selected.fetch_members()
    created = existing = added = already = removed = 0
    multi_before = await _multi_membership_count(session)
    touched_ids: list[int] = []
    keep_ids: set[int] = set()
    for member in members:
        company, was_created = await _company_for_member(session, member, selected.source)
        if was_created:
            created += 1
        else:
            existing += 1
            _fill_blanks(company, member)
        membership, is_new = await add_company_to_universe(
            session,
            company.id,
            selected.universe_name,
            selected.source,
            universe_status="SCREENED",
            discovery_reason=f"Index import {selected.universe_name}",
            metadata_json=member.source_metadata or None,
            commit=False,
        )
        if is_new:
            added += 1
        else:
            already += 1
            if member.source_metadata:
                membership.metadata_json = member.source_metadata
        keep_ids.add(company.id)
        touched_ids.append(company.id)
    removed = await _deactivate_missing(session, selected.universe_name, selected.source, keep_ids)
    for company_id in sorted(set(touched_ids)):
        company = await session.get(Company, company_id)
        if company is not None:
            await recalculate_universe_priority(session, company)
    await session.commit()
    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    _store_refresh(selected.universe_name, stamp)
    multi_after = await _multi_membership_count(session)
    result = {
        "universe": selected.universe_name,
        "provider": selected.provider_name,
        "source_documentation": selected.documentation,
        "fetched": len(members),
        "created_companies": created,
        "existing_companies": existing,
        "memberships_added": added,
        "memberships_existing": already,
        "memberships_removed": removed,
        "multi_universe_companies": multi_after,
        "multi_universe_delta": multi_after - multi_before,
        "last_refresh": stamp,
    }
    logger.info(
        "universe_refresh_finished universe=%s fetched=%s created=%s existing=%s added=%s removed=%s",
        selected.universe_name,
        len(members),
        created,
        existing,
        added,
        removed,
    )
    return result


async def refresh_sp500(session: AsyncSession, *, provider: UniverseProvider | None = None) -> dict:
    return await refresh_universe(session, "SP500", provider=provider)


async def refresh_nasdaq100(session: AsyncSession, *, provider: UniverseProvider | None = None) -> dict:
    return await refresh_universe(session, "NASDAQ100", provider=provider)


def last_refresh(universe_name: str) -> str | None:
    try:
        raw = redis_connection().get(REFRESH_KEY.format(universe=universe_name.upper()))
    except Exception:
        return None
    if raw is None:
        return None
    if isinstance(raw, bytes):
        return raw.decode()
    return str(raw)


def _store_refresh(universe_name: str, stamp: str) -> None:
    try:
        redis_connection().set(REFRESH_KEY.format(universe=universe_name.upper()), stamp)
    except Exception as exc:
        logger.warning("universe_refresh_stamp_failed error_type=%s", type(exc).__name__)


async def _company_for_member(
    session: AsyncSession,
    member: NormalizedMember,
    source: str,
) -> tuple[Company, bool]:
    company = await _find_company(session, member)
    if company is not None:
        return company, False
    company = Company(
        name=member.name,
        ticker=member.ticker,
        country=member.country,
        exchange=member.exchange,
        sector=member.sector,
        industry=member.industry,
        isin=member.isin,
        sec_cik=member.sec_cik,
        market_symbol=member.market_symbol or member.ticker,
        universe_status="SCREENED",
        discovery_source=source,
        discovery_reason=f"Index import {source}",
        is_active=True,
        first_seen_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc),
    )
    session.add(company)
    await session.flush()
    return company, True


async def _find_company(session: AsyncSession, member: NormalizedMember) -> Company | None:
    by_ticker = await session.scalar(select(Company).where(Company.ticker == member.ticker))
    if by_ticker is not None:
        return by_ticker
    if member.sec_cik:
        by_cik = await session.scalar(select(Company).where(Company.sec_cik == member.sec_cik))
        if by_cik is not None:
            return by_cik
    if member.isin:
        by_isin = await session.scalar(select(Company).where(Company.isin == member.isin))
        if by_isin is not None:
            return by_isin
    return None


def _fill_blanks(company: Company, member: NormalizedMember) -> None:
    if not company.country and member.country:
        company.country = member.country
    if not company.exchange and member.exchange:
        company.exchange = member.exchange
    if not company.sector and member.sector:
        company.sector = member.sector
    if not company.industry and member.industry:
        company.industry = member.industry
    if not company.isin and member.isin:
        company.isin = member.isin
    if not company.sec_cik and member.sec_cik:
        company.sec_cik = member.sec_cik
    if not company.market_symbol:
        company.market_symbol = member.market_symbol or member.ticker
    if not company.name and member.name:
        company.name = member.name


async def _deactivate_missing(
    session: AsyncSession,
    universe_name: str,
    source: str,
    keep_ids: set[int],
) -> int:
    statement = select(UniverseMembership).where(
        UniverseMembership.universe_name == universe_name,
        UniverseMembership.source == source,
        UniverseMembership.is_active.is_(True),
    )
    removed = 0
    now = datetime.now(timezone.utc)
    for membership in (await session.execute(statement)).scalars():
        if membership.company_id in keep_ids:
            continue
        membership.is_active = False
        membership.removed_at = now
        removed += 1
    return removed


async def _multi_membership_count(session: AsyncSession) -> int:
    from sqlalchemy import func

    statement = (
        select(func.count())
        .select_from(
            select(UniverseMembership.company_id)
            .where(UniverseMembership.is_active.is_(True))
            .group_by(UniverseMembership.company_id)
            .having(func.count(func.distinct(UniverseMembership.universe_name)) > 1)
            .subquery()
        )
    )
    return int((await session.execute(statement)).scalar_one())
