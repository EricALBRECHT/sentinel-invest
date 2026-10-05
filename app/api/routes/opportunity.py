from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.company import Company
from app.models.company_score import CompanyScore
from app.models.opportunity_profile import OpportunityProfile
from app.models.opportunity_score import OpportunityScore
from app.schemas.company import CompanyRead
from app.schemas.opportunity import (
    AnalysisSummaryRead,
    OpportunityComponentRead,
    OpportunityProfileRead,
    OpportunityProfileWrite,
    OpportunityScoreRead,
    OpportunitySummary,
    ScoreSummary,
)
from app.services.analysis.opportunity_recalculate import recalculate_opportunity_score
from app.services.analysis.opportunity_thresholds import METHOD_VERSION
from app.services.analysis.thresholds import METHOD_VERSION as QUALITY_VERSION

router = APIRouter(
    prefix="/companies",
    tags=["opportunity"],
    dependencies=[Depends(get_current_user)],
)

_PROFILE_FIELDS = (
    "market_growth_score",
    "market_size_score",
    "market_penetration_score",
    "strategic_position_score",
    "bottleneck_score",
    "supplier_leverage_score",
    "customer_diversification_score",
    "innovation_score",
    "rd_intensity_score",
    "capacity_expansion_score",
    "geographic_expansion_score",
    "competitive_moat_score",
    "competition_risk_score",
    "megatrends_json",
    "strategic_roles_json",
    "customers_json",
    "suppliers_json",
    "evidence_json",
    "data_confidence",
    "analysis_date",
)


async def _get_company_or_404(db: AsyncSession, company_id: int) -> Company:
    company = await db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    return company


def _profile_read(company: Company, row: OpportunityProfile) -> OpportunityProfileRead:
    return OpportunityProfileRead(
        id=row.id,
        company_id=company.id,
        ticker=company.ticker,
        megatrend_score=row.megatrend_score,
        market_growth_score=row.market_growth_score,
        market_size_score=row.market_size_score,
        market_penetration_score=row.market_penetration_score,
        strategic_position_score=row.strategic_position_score,
        bottleneck_score=row.bottleneck_score,
        supplier_leverage_score=row.supplier_leverage_score,
        customer_diversification_score=row.customer_diversification_score,
        innovation_score=row.innovation_score,
        rd_intensity_score=row.rd_intensity_score,
        capacity_expansion_score=row.capacity_expansion_score,
        geographic_expansion_score=row.geographic_expansion_score,
        size_runway_score=row.size_runway_score,
        competitive_moat_score=row.competitive_moat_score,
        competition_risk_score=row.competition_risk_score,
        megatrends_json=row.megatrends_json,
        strategic_roles_json=row.strategic_roles_json,
        customers_json=row.customers_json,
        suppliers_json=row.suppliers_json,
        evidence_json=row.evidence_json,
        data_confidence=row.data_confidence,
        analysis_date=row.analysis_date,
        method_version=row.method_version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _score_read(company: Company, row: OpportunityScore) -> OpportunityScoreRead:
    payload = row.components_json or {}
    components = {
        name: OpportunityComponentRead.model_validate(body)
        for name, body in payload.items()
        if isinstance(body, dict) and "included" in body
    }
    return OpportunityScoreRead(
        id=row.id,
        company_id=company.id,
        ticker=company.ticker,
        score_date=row.score_date,
        method_version=row.method_version,
        opportunity_score=row.opportunity_score,
        opportunity_confidence_score=row.opportunity_confidence_score,
        coverage_score=row.coverage_score,
        coverage_status=row.coverage_status,
        ranking_eligible=row.ranking_eligible,
        components=components,
        available_weight=payload.get("available_weight") or 0,
        excluded=list(payload.get("excluded") or []),
        revenue_growth_acceleration=payload.get("revenue_growth_acceleration"),
        notes=list(payload.get("notes") or []),
        created_at=row.created_at,
    )


@router.put("/{company_id}/opportunity-profile", response_model=OpportunityProfileRead)
async def upsert_opportunity_profile(
    company_id: int,
    payload: OpportunityProfileWrite,
    db: AsyncSession = Depends(get_db),
) -> OpportunityProfileRead:
    company = await _get_company_or_404(db, company_id)
    statement = select(OpportunityProfile).where(OpportunityProfile.company_id == company.id)
    row = (await db.execute(statement)).scalar_one_or_none()
    if row is None:
        row = OpportunityProfile(company_id=company.id, method_version=METHOD_VERSION)
        db.add(row)
    changes = payload.model_dump(exclude_unset=True)
    for field in _PROFILE_FIELDS:
        if field in changes:
            value = changes[field]
            if field.endswith("_json"):
                value = _json_ready(value)
            setattr(row, field, value)
    await db.commit()
    await db.refresh(row)
    return _profile_read(company, row)


@router.get("/{company_id}/opportunity-profile", response_model=OpportunityProfileRead)
async def get_opportunity_profile(
    company_id: int,
    db: AsyncSession = Depends(get_db),
) -> OpportunityProfileRead:
    company = await _get_company_or_404(db, company_id)
    statement = select(OpportunityProfile).where(OpportunityProfile.company_id == company.id)
    row = (await db.execute(statement)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No opportunity profile for this company")
    return _profile_read(company, row)


@router.post("/{company_id}/scores/opportunity/recalculate", response_model=OpportunityScoreRead)
async def recalculate_company_opportunity_score(
    company_id: int,
    db: AsyncSession = Depends(get_db),
) -> OpportunityScoreRead:
    company = await _get_company_or_404(db, company_id)
    row = await recalculate_opportunity_score(db, company)
    return _score_read(company, row)


@router.get("/{company_id}/scores/opportunity/latest", response_model=OpportunityScoreRead)
async def get_latest_opportunity_score(
    company_id: int,
    db: AsyncSession = Depends(get_db),
) -> OpportunityScoreRead:
    company = await _get_company_or_404(db, company_id)
    row = await _latest_opportunity(db, company.id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No opportunity score for this company")
    return _score_read(company, row)


@router.get("/{company_id}/scores/opportunity/history", response_model=list[OpportunityScoreRead])
async def get_opportunity_history(
    company_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[OpportunityScoreRead]:
    company = await _get_company_or_404(db, company_id)
    statement = (
        select(OpportunityScore)
        .where(OpportunityScore.company_id == company.id)
        .order_by(OpportunityScore.score_date.desc(), OpportunityScore.id.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = (await db.execute(statement)).scalars().all()
    return [_score_read(company, row) for row in rows]


@router.get("/{company_id}/analysis-summary", response_model=AnalysisSummaryRead)
async def get_analysis_summary(
    company_id: int,
    db: AsyncSession = Depends(get_db),
) -> AnalysisSummaryRead:
    company = await _get_company_or_404(db, company_id)
    quality = await _latest_quality(db, company.id)
    opportunity = await _latest_opportunity(db, company.id)
    return AnalysisSummaryRead(
        company=CompanyRead.model_validate(company),
        quality=ScoreSummary(
            score=None if quality is None else quality.quality_score,
            confidence=None if quality is None else quality.overall_confidence_score,
            method_version=None if quality is None else quality.method_version,
        ),
        opportunity=OpportunitySummary(
            score=None if opportunity is None else opportunity.opportunity_score,
            confidence=None if opportunity is None else opportunity.opportunity_confidence_score,
            coverage=None if opportunity is None else opportunity.coverage_score,
            status=None if opportunity is None else opportunity.coverage_status,
            ranking_eligible=None if opportunity is None else opportunity.ranking_eligible,
            method_version=None if opportunity is None else opportunity.method_version,
        ),
    )


def _json_ready(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    return value


async def _latest_quality(db: AsyncSession, company_id: int) -> CompanyScore | None:
    statement = (
        select(CompanyScore)
        .where(
            CompanyScore.company_id == company_id,
            CompanyScore.method_version == QUALITY_VERSION,
        )
        .order_by(CompanyScore.score_date.desc(), CompanyScore.id.desc())
        .limit(1)
    )
    return (await db.execute(statement)).scalar_one_or_none()


async def _latest_opportunity(db: AsyncSession, company_id: int) -> OpportunityScore | None:
    statement = (
        select(OpportunityScore)
        .where(
            OpportunityScore.company_id == company_id,
            OpportunityScore.method_version == METHOD_VERSION,
        )
        .order_by(OpportunityScore.score_date.desc(), OpportunityScore.id.desc())
        .limit(1)
    )
    return (await db.execute(statement)).scalar_one_or_none()
