"""Which companies are due for a news poll.

PORTFOLIO is checked every 2 hours, DEEP_ANALYSIS every 4, WATCHED every 12,
and every other active company with a linked source once a day. The scheduler
itself wakes on the shortest of those intervals.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.company_sync_status import CompanySyncStatus
from app.models.intelligence import CompanyExternalSource, ExternalSource
from app.services.intelligence.sources import NEWS_SYNC_SOURCE

INTERVAL_HOURS = {
    "PORTFOLIO": 2,
    "DEEP_ANALYSIS": 4,
    "WATCHED": 12,
}
DEFAULT_INTERVAL_HOURS = 24


def interval_hours(universe_status: str) -> int:
    return INTERVAL_HOURS.get(universe_status, DEFAULT_INTERVAL_HOURS)


async def select_due_news_company_ids(session: AsyncSession, *, now: datetime | None = None) -> list[int]:
    moment = now or datetime.now(timezone.utc)
    sources = (await session.scalars(select(ExternalSource).where(ExternalSource.is_active.is_(True)))).all()
    linked = {int(source.metadata_json.get("company_id")) for source in sources if _company_id(source) is not None}
    linked.update(
        int(company_id)
        for company_id in (
            await session.scalars(select(CompanyExternalSource.company_id).distinct())
        ).all()
    )
    if not linked:
        return []
    companies = (
        await session.scalars(select(Company).where(Company.is_active.is_(True), Company.id.in_(linked)))
    ).all()
    statuses = {
        row.company_id: row
        for row in (
            await session.scalars(select(CompanySyncStatus).where(CompanySyncStatus.source == NEWS_SYNC_SOURCE))
        ).all()
    }
    due = []
    for company in companies:
        status = statuses.get(company.id)
        if status is None or status.last_success_at is None or _older_than(status.last_success_at, moment, interval_hours(company.universe_status)):
            due.append(company)
    due.sort(key=lambda company: (_rank(company.universe_status), -int(company.universe_priority or 0), company.id))
    return [company.id for company in due]


def _company_id(source: ExternalSource) -> int | None:
    value = (source.metadata_json or {}).get("company_id")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _older_than(moment: datetime, now: datetime, hours: int) -> bool:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment < now - timedelta(hours=hours)


def _rank(status: str) -> int:
    if status == "PORTFOLIO":
        return 0
    if status == "DEEP_ANALYSIS":
        return 1
    if status == "WATCHED":
        return 2
    return 3
