"""Score recalculation used by the analysis queue."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.opportunity_profile import OpportunityProfile
from app.services.analysis.opportunity_recalculate import recalculate_opportunity_score
from app.services.analysis.recalculate import recalculate_quality_score


async def execute_quality(session: AsyncSession, company_id: int) -> dict:
    company = await session.get(Company, company_id)
    if company is None:
        return {"company_id": company_id, "status": "missing_company"}
    ticker = company.ticker
    row = await recalculate_quality_score(session, company)
    return {
        "company_id": company_id,
        "ticker": ticker,
        "status": "success",
        "quality_score": None if row.quality_score is None else format(row.quality_score, "f"),
    }


async def execute_opportunity(session: AsyncSession, company_id: int) -> dict:
    company = await session.get(Company, company_id)
    if company is None:
        return {"company_id": company_id, "status": "missing_company"}
    ticker = company.ticker
    statement = select(OpportunityProfile.id).where(OpportunityProfile.company_id == company_id)
    if (await session.execute(statement)).scalar_one_or_none() is None:
        return {"company_id": company_id, "ticker": ticker, "status": "SKIPPED_NO_PROFILE"}
    row = await recalculate_opportunity_score(session, company)
    return {
        "company_id": company_id,
        "ticker": ticker,
        "status": "success",
        "opportunity_score": None if row.opportunity_score is None else format(row.opportunity_score, "f"),
    }
