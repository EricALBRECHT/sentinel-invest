"""Which discovered companies may be expanded, and how often."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.company import Company
from app.models.supply_chain import DiscoveredCompany
from app.services.discovery_expansion.sources import sec_financial_eligible


async def assign_lineage(session: AsyncSession, company: Company) -> str | None:
    """Set the discovery parent from the promoted candidate. A cycle blocks expansion."""
    if company.discovered_parent_company_id is None:
        candidate = await session.scalar(
            select(DiscoveredCompany)
            .where(DiscoveredCompany.promoted_company_id == company.id)
            .order_by(DiscoveredCompany.id.asc())
        )
        parent_id = None if candidate is None else candidate.discovered_from_company_id
        if parent_id is not None and parent_id != company.id:
            parent = await session.get(Company, parent_id)
            if parent is not None:
                company.discovered_parent_company_id = parent.id
    depth = await _depth(session, company)
    if depth < 0:
        company.discovery_pipeline_status = "BLOCKED"
        return "cycle"
    company.discovery_depth = depth
    return None


def expansion_block_reason(company: Company) -> str | None:
    if not company.is_active:
        return "inactive"
    if int(company.discovery_depth or 0) > settings.discovery_max_depth:
        return "max_depth"
    return None


async def select_due_expansion_ids(session: AsyncSession, *, now: datetime | None = None) -> list[int]:
    moment = now or datetime.now(timezone.utc)
    companies = list(
        (
            await session.scalars(
                select(Company)
                .where(
                    Company.is_active.is_(True),
                    Company.universe_status == "DISCOVERED",
                    Company.discovery_pipeline_status != "BLOCKED",
                    Company.discovery_depth <= settings.discovery_max_depth,
                    Company.sec_cik.is_not(None),
                    Company.sec_cik != "",
                )
                .order_by(Company.discovery_depth.asc(), Company.id.asc())
            )
        ).all()
    )
    due = [company.id for company in companies if sec_financial_eligible(company) and _is_due(company, moment)]
    return due[: max(0, settings.discovery_expansion_max_per_run)]


def _is_due(company: Company, moment: datetime) -> bool:
    last = company.last_discovery_collection_at
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    hours = (
        settings.discovery_expansion_analyzed_hours
        if company.discovery_pipeline_status == "ANALYZED"
        else settings.discovery_expansion_scan_hours
    )
    return last < moment - timedelta(hours=max(1, hours))


async def _depth(session: AsyncSession, company: Company) -> int:
    seen = {company.id}
    current = company.discovered_parent_company_id
    depth = 0
    while current is not None:
        if current in seen or depth > 20:
            return -1
        seen.add(current)
        parent = await session.get(Company, current)
        if parent is None:
            break
        depth += 1
        current = parent.discovered_parent_company_id
    return depth
