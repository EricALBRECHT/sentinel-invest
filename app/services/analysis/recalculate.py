"""Persist a quality_v1 score computed only from FY periods."""

from datetime import datetime, timezone
from decimal import Decimal
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.company_score import CompanyScore
from app.models.financial_metric import FinancialMetric
from app.services.analysis.annual_metrics import AnnualMetrics, AnnualSnapshot, build_annual_metrics
from app.services.analysis.confidence import build_confidence, overall_confidence_score
from app.services.analysis.quality_score import QualityScore, build_quality_score, splits_as_dicts
from app.services.analysis.thresholds import METHOD_VERSION

logger = logging.getLogger("sentinel.analysis")

_METRIC_FIELDS = (
    "revenue_growth_1y",
    "revenue_cagr_3y",
    "revenue_cagr_5y",
    "net_income_growth_1y",
    "net_income_cagr_3y",
    "net_income_cagr_5y",
    "fcf_growth_1y",
    "fcf_cagr_3y",
    "fcf_cagr_5y",
    "gross_margin",
    "operating_margin",
    "net_margin",
    "fcf_margin",
    "debt_to_equity",
    "debt_to_fcf",
    "cash_to_debt",
    "roe",
    "roa",
    "shares_growth_1y",
    "shares_cagr_3y",
    "shares_cagr_5y",
)


async def recalculate_quality_score(
    db: AsyncSession,
    company: Company,
    *,
    capex_fallback: bool = False,
) -> CompanyScore:
    snapshots = await _load_annual_snapshots(db, company.id)
    metrics = build_annual_metrics(snapshots)
    confidence = build_confidence(snapshots, metrics, capex_fallback=capex_fallback)
    score = build_quality_score(snapshots, metrics)
    overall = overall_confidence_score(confidence.levels(), score.available_weight)
    score_date = datetime.now(timezone.utc).date()
    payload = {
        "quality_score": score.quality_score,
        "overall_confidence_score": overall,
        "revenue_growth_score": score.components["revenue_growth"],
        "profit_growth_score": score.components["profit_growth"],
        "fcf_growth_score": score.components["fcf_growth"],
        "margins_score": score.components["margins"],
        "profitability_score": score.components["profitability"],
        "debt_score": score.components["debt"],
        "cash_score": score.components["cash"],
        "dilution_score": score.components["dilution"],
        "stability_score": score.components["stability"],
        "metrics_json": _metrics_json(metrics, score),
        "confidence_json": {
            **confidence.as_json(),
            "anomalies": list(score.anomalies),
        },
    }
    row = await _upsert(db, company.id, score_date, payload)
    logger.info(
        "quality_score_saved company_id=%s ticker=%s fiscal_year=%s quality_score=%s confidence=%s",
        company.id,
        company.ticker,
        metrics.latest_fiscal_year,
        score.quality_score,
        overall,
    )
    return row


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
            revenue_source_concept=row.revenue_source_concept,
            net_income_source_concept=row.net_income_source_concept,
            operating_cash_flow_source_concept=row.operating_cash_flow_source_concept,
            capital_expenditure_source_concept=row.capital_expenditure_source_concept,
            debt_source_concept=row.debt_source_concept,
            shares_source_concept=row.shares_source_concept,
        )
        for row in by_year.values()
    ]


async def _upsert(db: AsyncSession, company_id: int, score_date, payload: dict) -> CompanyScore:
    statement = select(CompanyScore).where(
        CompanyScore.company_id == company_id,
        CompanyScore.score_date == score_date,
        CompanyScore.method_version == METHOD_VERSION,
    )
    existing = (await db.execute(statement)).scalar_one_or_none()
    if existing is None:
        existing = CompanyScore(
            company_id=company_id,
            score_date=score_date,
            method_version=METHOD_VERSION,
            **payload,
        )
        db.add(existing)
    else:
        for field, value in payload.items():
            setattr(existing, field, value)
    await db.commit()
    await db.refresh(existing)
    return existing


def _metrics_json(metrics: AnnualMetrics, score: QualityScore) -> dict:
    payload: dict = {
        name: _json_number(getattr(metrics, name))
        for name in _METRIC_FIELDS
    }
    payload["latest_fiscal_year"] = metrics.latest_fiscal_year
    payload["shares_comparable"] = metrics.shares_comparable
    payload["share_splits"] = splits_as_dicts(metrics.share_splits)
    payload["revenue_growth_basis"] = score.growth_basis["revenue_growth"]
    payload["profit_growth_basis"] = score.growth_basis["profit_growth"]
    payload["fcf_growth_basis"] = score.growth_basis["fcf_growth"]
    return payload


def _json_number(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")
