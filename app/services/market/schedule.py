"""Which active universe companies are due for a daily market sync."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.company import Company
from app.models.company_sync_status import CompanySyncStatus
from app.models.universe_membership import UniverseMembership
from app.services.market.provider import MARKET_SOURCE


async def select_due_market_company_ids(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    interval_hours: int | None = None,
) -> list[int]:
    moment = now or datetime.now(timezone.utc)
    hours = settings.market_sync_interval_hours if interval_hours is None else interval_hours
    cutoff = moment - timedelta(hours=hours)
    statement = (
        select(Company.id, Company.universe_status, Company.universe_priority)
        .join(
            UniverseMembership,
            (UniverseMembership.company_id == Company.id) & (UniverseMembership.is_active.is_(True)),
        )
        .outerjoin(
            CompanySyncStatus,
            (CompanySyncStatus.company_id == Company.id) & (CompanySyncStatus.source == MARKET_SOURCE),
        )
        .where(Company.is_active.is_(True))
        .where(Company.market_symbol.is_not(None), Company.market_symbol != "")
        .where(
            or_(
                CompanySyncStatus.id.is_(None),
                CompanySyncStatus.last_success_at.is_(None),
                CompanySyncStatus.last_success_at < cutoff,
            )
        )
    )
    rows = (await session.execute(statement)).all()
    ordered = sorted(rows, key=_rank)
    seen: set[int] = set()
    company_ids: list[int] = []
    for row in ordered:
        if row.id in seen:
            continue
        seen.add(row.id)
        company_ids.append(row.id)
    return company_ids


def _rank(row) -> tuple[int, int, int]:
    if row.universe_status == "PORTFOLIO":
        status_rank = 0
    elif row.universe_status == "DEEP_ANALYSIS":
        status_rank = 1
    else:
        status_rank = 2
    return (status_rank, -int(row.universe_priority or 0), int(row.id))
