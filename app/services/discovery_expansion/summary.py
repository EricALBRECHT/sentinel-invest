"""A small count of the discovery tree. It does not walk documents."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.supply_chain import CompanyRelationship, DiscoveredCompany


async def graph_summary(session: AsyncSession) -> dict:
    depths = (
        await session.execute(select(Company.discovery_depth, func.count()).group_by(Company.discovery_depth))
    ).all()
    open_candidates = int(
        (
            await session.execute(
                select(func.count())
                .select_from(DiscoveredCompany)
                .where(DiscoveredCompany.promoted_company_id.is_(None))
            )
        ).scalar_one()
    )
    relationships = int((await session.execute(select(func.count()).select_from(CompanyRelationship))).scalar_one())
    return {
        "companies_by_depth": {str(depth): int(count) for depth, count in depths},
        "open_candidates": open_candidates,
        "relationships": relationships,
    }


async def expansion_counts(session: AsyncSession) -> dict:
    from app.core.config import settings

    rows = (
        await session.execute(
            select(Company.discovery_pipeline_status, func.count()).group_by(Company.discovery_pipeline_status)
        )
    ).all()
    counted = {status: int(count) for status, count in rows}
    deepest = (
        await session.execute(
            select(Company.ticker, Company.discovery_depth)
            .order_by(Company.discovery_depth.desc(), Company.id.asc())
            .limit(1)
        )
    ).first()
    last_expansion = await session.scalar(select(func.max(Company.last_discovery_collection_at)))
    return {
        "ready": counted.get("READY", 0),
        "collecting": counted.get("COLLECTING", 0),
        "analyzed": counted.get("ANALYZED", 0),
        "blocked": counted.get("BLOCKED", 0),
        "max_depth": settings.discovery_max_depth,
        "deepest_company": None if deepest is None else deepest.ticker,
        "last_expansion": last_expansion,
    }
