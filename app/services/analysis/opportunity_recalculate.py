"""Persist an opportunity_v1 score. Quality scores are left untouched."""

from datetime import datetime, timezone
from decimal import Decimal
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.financial_metric import FinancialMetric
from app.models.opportunity_profile import OpportunityProfile
from app.models.opportunity_score import OpportunityScore
from app.services.analysis.annual_metrics import AnnualSnapshot, build_annual_metrics
from app.services.analysis.opportunity_score import (
    CustomerInput,
    MegatrendInput,
    OpportunityAssessment,
    OpportunityComponent,
    OpportunityInputs,
    build_opportunity_score,
)
from app.services.analysis.opportunity_thresholds import METHOD_VERSION

logger = logging.getLogger("sentinel.analysis")

_COLUMN_FOR = {
    "growth_runway": "growth_runway_score",
    "size_runway": "size_runway_score",
    "megatrend": "megatrend_score",
    "market": "market_score",
    "strategic_position": "strategic_position_score",
    "bottleneck": "bottleneck_score",
    "innovation": "innovation_score",
    "moat": "moat_score",
    "expansion": "expansion_score",
}


async def recalculate_opportunity_score(db: AsyncSession, company: Company) -> OpportunityScore:
    snapshots = await _load_annual_snapshots(db, company.id)
    metrics = build_annual_metrics(snapshots)
    profile = await _load_profile(db, company.id)
    assessment = build_opportunity_score(_inputs(company, profile, snapshots, metrics))
    if profile is not None:
        profile.megatrend_score = assessment.components["megatrend"].score
        profile.size_runway_score = assessment.components["size_runway"].score
        profile.method_version = METHOD_VERSION
    score_date = datetime.now(timezone.utc).date()
    payload = _payload(assessment)
    row = await _upsert(db, company.id, score_date, payload)
    logger.info(
        "opportunity_score_saved company_id=%s ticker=%s opportunity_score=%s confidence=%s available_weight=%s",
        company.id,
        company.ticker,
        assessment.opportunity_score,
        assessment.opportunity_confidence_score,
        assessment.available_weight,
    )
    return row


async def _load_profile(db: AsyncSession, company_id: int) -> OpportunityProfile | None:
    statement = select(OpportunityProfile).where(OpportunityProfile.company_id == company_id)
    return (await db.execute(statement)).scalar_one_or_none()


async def _load_annual_snapshots(db: AsyncSession, company_id: int) -> list[AnnualSnapshot]:
    statement = (
        select(FinancialMetric)
        .where(
            FinancialMetric.company_id == company_id,
            FinancialMetric.fiscal_period == "FY",
        )
        .order_by(FinancialMetric.fiscal_year, FinancialMetric.filed_at, FinancialMetric.id)
    )
    rows = (await db.execute(statement)).scalars().all()
    by_year: dict[int, FinancialMetric] = {}
    for row in rows:
        by_year[row.fiscal_year] = row
    return [
        AnnualSnapshot(
            fiscal_year=row.fiscal_year,
            revenue=row.revenue,
            gross_profit=row.gross_profit,
            operating_income=row.operating_income,
            net_income=row.net_income,
            free_cash_flow=row.free_cash_flow,
            cash_and_equivalents=row.cash_and_equivalents,
            total_assets=row.total_assets,
            total_debt=row.total_debt,
            shareholders_equity=row.shareholders_equity,
            shares_outstanding=row.shares_outstanding,
        )
        for row in by_year.values()
    ]


def _inputs(
    company: Company,
    profile: OpportunityProfile | None,
    snapshots: list[AnnualSnapshot],
    metrics,
) -> OpportunityInputs:
    if profile is None:
        return OpportunityInputs(snapshots=snapshots, metrics=metrics, market_cap=company.market_cap)
    return OpportunityInputs(
        snapshots=snapshots,
        metrics=metrics,
        market_cap=company.market_cap,
        market_growth_score=profile.market_growth_score,
        market_size_score=profile.market_size_score,
        market_penetration_score=profile.market_penetration_score,
        strategic_position_score=profile.strategic_position_score,
        supplier_leverage_score=profile.supplier_leverage_score,
        customer_diversification_score=profile.customer_diversification_score,
        bottleneck_score=profile.bottleneck_score,
        innovation_score=profile.innovation_score,
        rd_intensity_score=profile.rd_intensity_score,
        capacity_expansion_score=profile.capacity_expansion_score,
        geographic_expansion_score=profile.geographic_expansion_score,
        competitive_moat_score=profile.competitive_moat_score,
        competition_risk_score=profile.competition_risk_score,
        megatrends=tuple(_megatrend(item) for item in profile.megatrends_json or []),
        strategic_roles=tuple(profile.strategic_roles_json or []),
        customers=tuple(_customer(item) for item in profile.customers_json or []),
        evidence=tuple(
            text
            for text in (_evidence_text(item) for item in profile.evidence_json or [])
            if text
        )
    )


def _megatrend(item: dict) -> MegatrendInput:
    return MegatrendInput(
        trend=str(item["trend"]),
        exposure_score=Decimal(str(item["exposure_score"])),
        confidence=int(item["confidence"]),
        evidence=item.get("evidence"),
        source=str(item.get("source") or "MANUAL_STRUCTURED"),
    )


def _customer(item: dict) -> CustomerInput:
    share = item.get("revenue_share")
    return CustomerInput(
        name=str(item["name"]),
        revenue_share=None if share is None else Decimal(str(share)),
    )


def _evidence_text(item: dict) -> str:
    component = item.get("component")
    text = str(item.get("text") or "")
    if component and component != "bottleneck":
        return ""
    return text


def _payload(assessment: OpportunityAssessment) -> dict:
    payload = {
        "opportunity_score": assessment.opportunity_score,
        "opportunity_confidence_score": assessment.opportunity_confidence_score,
        "coverage_score": assessment.coverage_score,
        "coverage_status": assessment.coverage_status,
        "ranking_eligible": assessment.ranking_eligible,
        "components_json": _components_json(assessment),
        "confidence_json": _confidence_json(assessment),
        "method_version": METHOD_VERSION,
    }
    for name, column in _COLUMN_FOR.items():
        payload[column] = assessment.components[name].score
    return payload


def _components_json(assessment: OpportunityAssessment) -> dict:
    payload: dict = {
        name: _component_dict(item)
        for name, item in assessment.components.items()
    }
    payload["available_weight"] = _number(assessment.available_weight)
    payload["coverage_score"] = assessment.coverage_score
    payload["coverage_status"] = assessment.coverage_status
    payload["ranking_eligible"] = assessment.ranking_eligible
    payload["revenue_growth_acceleration"] = _number(assessment.acceleration)
    payload["excluded"] = list(assessment.excluded)
    payload["notes"] = list(assessment.notes)
    return payload


def _confidence_json(assessment: OpportunityAssessment) -> dict:
    return {
        "opportunity_confidence_score": assessment.opportunity_confidence_score,
        "components": {
            name: {
                "confidence": item.confidence,
                "source": item.source,
                "evidence": item.evidence,
            }
            for name, item in assessment.components.items()
        },
        "notes": list(assessment.notes),
    }


def _component_dict(item: OpportunityComponent) -> dict:
    return {
        "score": _number(item.score),
        "weight": _number(item.weight),
        "points": _number(item.points),
        "confidence": item.confidence,
        "source": item.source,
        "evidence": item.evidence,
        "included": item.included,
    }


def _number(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


async def _upsert(db: AsyncSession, company_id: int, score_date, payload: dict) -> OpportunityScore:
    statement = select(OpportunityScore).where(
        OpportunityScore.company_id == company_id,
        OpportunityScore.score_date == score_date,
        OpportunityScore.method_version == METHOD_VERSION,
    )
    existing = (await db.execute(statement)).scalar_one_or_none()
    if existing is None:
        existing = OpportunityScore(
            company_id=company_id,
            score_date=score_date,
            **payload,
        )
        db.add(existing)
    else:
        for field, value in payload.items():
            setattr(existing, field, value)
    await db.commit()
    await db.refresh(existing)
    return existing
