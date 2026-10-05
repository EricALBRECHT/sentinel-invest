"""Which companies are due for an SEC sync. This does not fetch EDGAR."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.company import Company
from app.models.company_sync_status import CompanySyncStatus
from app.services.sec.mappings import SEC_SOURCE


async def select_due_company_ids(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    interval_hours: int | None = None,
) -> list[int]:
    moment = now or datetime.now(timezone.utc)
    hours = settings.sec_sync_interval_hours if interval_hours is None else interval_hours
    cutoff = moment - timedelta(hours=hours)
    statement = (
        select(Company.id)
        .outerjoin(
            CompanySyncStatus,
            (CompanySyncStatus.company_id == Company.id) & (CompanySyncStatus.source == SEC_SOURCE),
        )
        .where(Company.sec_cik.is_not(None), Company.sec_cik != "")
        .where(
            or_(
                CompanySyncStatus.id.is_(None),
                CompanySyncStatus.last_success_at.is_(None),
                CompanySyncStatus.last_success_at < cutoff,
            )
        )
        .order_by(CompanySyncStatus.last_success_at.asc().nullsfirst(), Company.id.asc())
    )
    return list((await session.execute(statement)).scalars().all())
