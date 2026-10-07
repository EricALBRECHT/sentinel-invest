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

# Outcomes for company identity handling during index refresh.
OUTCOME_CREATED = "created"
OUTCOME_UPDATED = "updated"
OUTCOME_EXISTING = "existing"
OUTCOME_SKIPPED = "skipped"
OUTCOME_CONFLICT = "conflict"


async def refresh_universe(
    session: AsyncSession,
    universe_name: str,
    *,
    provider: UniverseProvider | None = None,
) -> dict:
    selected = provider or provider_for(universe_name)
    members = await selected.fetch_members()
    created = updated = existing = skipped = conflict = 0
    added = already = removed = 0
    multi_before = await _multi_membership_count(session)
    touched_ids: list[int] = []
    keep_ids: set[int] = set()
    for member in members:
        company, outcome, cik_conflict = await _company_for_member(session, member, selected.source)
        if outcome == OUTCOME_CREATED:
            created += 1
        elif outcome == OUTCOME_UPDATED:
            updated += 1
        elif outcome == OUTCOME_EXISTING:
            existing += 1
        elif outcome == OUTCOME_SKIPPED:
            skipped += 1
        elif outcome == OUTCOME_CONFLICT:
            conflict += 1
        if cik_conflict and outcome != OUTCOME_CONFLICT:
            conflict += 1
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
        "updated_companies": updated,
        "existing_companies": existing,
        "skipped_companies": skipped,
        "conflict_companies": conflict,
        "memberships_added": added,
        "memberships_existing": already,
        "memberships_removed": removed,
        "multi_universe_companies": multi_after,
        "multi_universe_delta": multi_after - multi_before,
        "last_refresh": stamp,
    }
    logger.info(
        "universe_refresh_finished universe=%s fetched=%s created=%s updated=%s existing=%s "
        "skipped=%s conflict=%s added=%s removed=%s",
        selected.universe_name,
        len(members),
        created,
        updated,
        existing,
        skipped,
        conflict,
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
) -> tuple[Company, str, bool]:
    company = await _find_company(session, member)
    if company is not None:
        outcome = await _apply_member_fields(session, company, member)
        return company, outcome, outcome == OUTCOME_CONFLICT

    cik, cik_conflict = await _cik_for_new_company(session, member)
    company = Company(
        name=member.name,
        ticker=member.ticker,
        country=member.country,
        exchange=member.exchange,
        sector=member.sector,
        industry=member.industry,
        isin=member.isin,
        sec_cik=cik,
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
    if cik_conflict:
        logger.warning(
            "universe_cik_conflict_on_create ticker=%s cik=%s kept_cik_null=1",
            member.ticker,
            member.sec_cik,
        )
    return company, OUTCOME_CREATED, cik_conflict


async def _find_company(session: AsyncSession, member: NormalizedMember) -> Company | None:
    """Resolve identity without merging distinct share classes that share a CIK."""
    by_ticker = await session.scalar(select(Company).where(Company.ticker == member.ticker))
    if by_ticker is not None:
        return by_ticker

    if member.isin:
        by_isin = await session.scalar(select(Company).where(Company.isin == member.isin))
        if by_isin is not None:
            return by_isin

    if member.sec_cik:
        owner = await _company_with_cik(session, member.sec_cik)
        if owner is not None and owner.ticker == member.ticker:
            return owner
        # Same CIK, different ticker (e.g. GOOG / GOOGL): do not merge.
    return None


async def _apply_member_fields(
    session: AsyncSession,
    company: Company,
    member: NormalizedMember,
) -> str:
    changed = False
    conflict = False

    def _set(attr: str, value: object | None) -> None:
        nonlocal changed
        if value in (None, ""):
            return
        current = getattr(company, attr)
        if current in (None, ""):
            setattr(company, attr, value)
            changed = True

    _set("country", member.country)
    _set("exchange", member.exchange)
    _set("sector", member.sector)
    _set("industry", member.industry)
    _set("isin", member.isin)
    if not company.market_symbol:
        company.market_symbol = member.market_symbol or member.ticker
        changed = True
    if not company.name and member.name:
        company.name = member.name
        changed = True

    if member.sec_cik:
        cik_result = await _assign_cik(session, company, member.sec_cik)
        if cik_result == OUTCOME_CONFLICT:
            conflict = True
        elif cik_result == OUTCOME_UPDATED:
            changed = True

    if conflict:
        return OUTCOME_CONFLICT
    if changed:
        return OUTCOME_UPDATED
    return OUTCOME_EXISTING


async def _assign_cik(session: AsyncSession, company: Company, sec_cik: str) -> str:
    """Attach CIK when safe. Never overwrite another company's unique CIK."""
    normalized = _canonical_cik(sec_cik)
    if not normalized:
        return OUTCOME_SKIPPED
    current = _canonical_cik(company.sec_cik)
    if current == normalized:
        return OUTCOME_EXISTING
    if company.sec_cik:
        # Company already has a different CIK — do not overwrite without merge proof.
        logger.warning(
            "universe_cik_conflict_existing_differs company_id=%s ticker=%s have=%s want=%s",
            company.id,
            company.ticker,
            company.sec_cik,
            sec_cik,
        )
        return OUTCOME_CONFLICT

    owner = await _company_with_cik(session, sec_cik)
    if owner is not None and owner.id != company.id:
        logger.warning(
            "universe_cik_conflict_owned company_id=%s ticker=%s cik=%s owner_id=%s owner_ticker=%s",
            company.id,
            company.ticker,
            sec_cik,
            owner.id,
            owner.ticker,
        )
        return OUTCOME_CONFLICT

    company.sec_cik = normalized
    return OUTCOME_UPDATED


async def _cik_for_new_company(
    session: AsyncSession,
    member: NormalizedMember,
) -> tuple[str | None, bool]:
    if not member.sec_cik:
        return None, False
    owner = await _company_with_cik(session, member.sec_cik)
    if owner is not None and owner.ticker != member.ticker:
        return None, True
    return _canonical_cik(member.sec_cik), False


async def _company_with_cik(session: AsyncSession, sec_cik: str) -> Company | None:
    variants = _cik_lookup_variants(sec_cik)
    if not variants:
        return None
    return await session.scalar(select(Company).where(Company.sec_cik.in_(variants)))


def _canonical_cik(value: str | None) -> str | None:
    if not value:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if not digits:
        return None
    # Match universe provider storage (zero-padded 10) used by index imports.
    return digits.zfill(10)[-10:]


def _cik_lookup_variants(value: str) -> list[str]:
    canonical = _canonical_cik(value)
    if not canonical:
        return []
    stripped = canonical.lstrip("0") or "0"
    variants = {canonical, stripped}
    return sorted(variants)


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
