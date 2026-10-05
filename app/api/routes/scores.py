from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.company import Company
from app.models.company_score import CompanyScore
from app.schemas.score import QualityScoreRead
from app.services.analysis.recalculate import recalculate_quality_score

router = APIRouter(
    prefix="/companies",
    tags=["scores"],
    dependencies=[Depends(get_current_user)],
)


async def _get_company_or_404(db: AsyncSession, company_id: int) -> Company:
    company = await db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    return company


def _to_read(company: Company, row: CompanyScore) -> QualityScoreRead:
    confidence = row.confidence_json or {}
    anomalies = list(confidence.get("anomalies") or [])
    return QualityScoreRead(
        id=row.id,
        company_id=company.id,
        ticker=company.ticker,
        score_date=row.score_date,
        method_version=row.method_version,
        quality_score=row.quality_score,
        overall_confidence_score=row.overall_confidence_score,
        metrics=row.metrics_json,
        components={
            "revenue_growth": row.revenue_growth_score,
            "profit_growth": row.profit_growth_score,
            "fcf_growth": row.fcf_growth_score,
            "margins": row.margins_score,
            "profitability": row.profitability_score,
            "debt": row.debt_score,
            "cash": row.cash_score,
            "dilution": row.dilution_score,
            "stability": row.stability_score,
        },
        confidence=confidence,
        anomalies=anomalies,
        created_at=row.created_at,
    )


@router.post("/{company_id}/scores/quality/recalculate", response_model=QualityScoreRead)
async def recalculate_company_quality_score(
    company_id: int,
    db: AsyncSession = Depends(get_db),
) -> QualityScoreRead:
    company = await _get_company_or_404(db, company_id)
    row = await recalculate_quality_score(db, company)
    return _to_read(company, row)


@router.get("/{company_id}/scores/latest", response_model=QualityScoreRead)
async def get_latest_score(
    company_id: int,
    db: AsyncSession = Depends(get_db),
) -> QualityScoreRead:
    company = await _get_company_or_404(db, company_id)
    statement = (
        select(CompanyScore)
        .where(CompanyScore.company_id == company.id)
        .order_by(CompanyScore.score_date.desc(), CompanyScore.id.desc())
        .limit(1)
    )
    row = (await db.execute(statement)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No quality score for this company")
    return _to_read(company, row)


@router.get("/{company_id}/scores/history", response_model=list[QualityScoreRead])
async def get_score_history(
    company_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[QualityScoreRead]:
    company = await _get_company_or_404(db, company_id)
    statement = (
        select(CompanyScore)
        .where(CompanyScore.company_id == company.id)
        .order_by(CompanyScore.score_date.desc(), CompanyScore.id.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = (await db.execute(statement)).scalars().all()
    return [_to_read(company, row) for row in rows]
