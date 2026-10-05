"""Persist one investment view per company, date, and method.

Inputs are the latest quality, opportunity, technical, and market rows on or
before the view date. Later rows are ignored.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.company_market_snapshot import CompanyMarketSnapshot
from app.models.company_score import CompanyScore
from app.models.investment_view import InvestmentView
from app.models.market_price import MarketPrice
from app.models.opportunity_score import OpportunityScore
from app.models.technical_snapshot import TechnicalSnapshot
from app.services.analysis.opportunity_thresholds import METHOD_VERSION as OPPORTUNITY_METHOD
from app.services.analysis.thresholds import METHOD_VERSION as QUALITY_METHOD
from app.services.investment_view.scoring import (
    METHOD,
    MarketInput,
    OpportunityInput,
    QualityInput,
    TechnicalInput,
    build_investment_view,
)
from app.services.technical.service import TECHNICAL_METHOD

RELIABLE_CAP = frozenset({"HIGH", "MEDIUM"})


async def recalculate_investment_view(
    session: AsyncSession,
    company_id: int,
    as_of_date: date | None = None,
) -> InvestmentView | None:
    company = await session.get(Company, company_id)
    if company is None:
        return None
    view_date = as_of_date if as_of_date is not None else await _current_date(session, company_id)
    quality = await _quality(session, company_id, view_date)
    opportunity = await _opportunity(session, company_id, view_date)
    technical = await _technical(session, company_id, view_date)
    market = await _market(session, company_id, view_date)
    assessment = build_investment_view(quality, opportunity, technical, market)
    row, _created = await _upsert(session, company_id, view_date)
    _apply(row, assessment)
    await session.commit()
    await session.refresh(row)
    return row


async def _current_date(session: AsyncSession, company_id: int) -> date:
    today = date.today()
    dates: list[date] = []
    quality_date = await session.scalar(
        select(func.max(CompanyScore.score_date)).where(
            CompanyScore.company_id == company_id,
            CompanyScore.method_version == QUALITY_METHOD,
            CompanyScore.score_date <= today,
        )
    )
    opportunity_date = await session.scalar(
        select(func.max(OpportunityScore.score_date)).where(
            OpportunityScore.company_id == company_id,
            OpportunityScore.method_version == OPPORTUNITY_METHOD,
            OpportunityScore.score_date <= today,
        )
    )
    technical_date = await session.scalar(
        select(func.max(TechnicalSnapshot.as_of_date)).where(
            TechnicalSnapshot.company_id == company_id,
            TechnicalSnapshot.method == TECHNICAL_METHOD,
            TechnicalSnapshot.as_of_date <= today,
        )
    )
    price_date = await session.scalar(
        select(func.max(MarketPrice.trade_date)).where(
            MarketPrice.company_id == company_id,
            MarketPrice.trade_date <= today,
        )
    )
    for value in (quality_date, opportunity_date, technical_date, price_date):
        if value is not None:
            dates.append(value)
    return max(dates) if dates else today


async def _quality(session: AsyncSession, company_id: int, view_date: date) -> QualityInput:
    row = await session.scalar(
        select(CompanyScore)
        .where(
            CompanyScore.company_id == company_id,
            CompanyScore.method_version == QUALITY_METHOD,
            CompanyScore.score_date <= view_date,
        )
        .order_by(CompanyScore.score_date.desc(), CompanyScore.id.desc())
        .limit(1)
    )
    if row is None or row.quality_score is None:
        return QualityInput(score=None, confidence=None, as_of_date=None if row is None else row.score_date)
    return QualityInput(
        score=Decimal(row.quality_score),
        confidence=row.overall_confidence_score,
        as_of_date=row.score_date,
    )


async def _opportunity(session: AsyncSession, company_id: int, view_date: date) -> OpportunityInput:
    row = await session.scalar(
        select(OpportunityScore)
        .where(
            OpportunityScore.company_id == company_id,
            OpportunityScore.method_version == OPPORTUNITY_METHOD,
            OpportunityScore.score_date <= view_date,
        )
        .order_by(OpportunityScore.score_date.desc(), OpportunityScore.id.desc())
        .limit(1)
    )
    if row is None:
        return OpportunityInput(None, None, None, None, None, None)
    return OpportunityInput(
        raw_score=None if row.opportunity_score is None else Decimal(row.opportunity_score),
        confidence=row.opportunity_confidence_score,
        coverage=row.coverage_score,
        coverage_status=row.coverage_status,
        ranking_eligible=row.ranking_eligible,
        as_of_date=row.score_date,
    )


async def _technical(session: AsyncSession, company_id: int, view_date: date) -> TechnicalInput:
    row = await session.scalar(
        select(TechnicalSnapshot)
        .where(
            TechnicalSnapshot.company_id == company_id,
            TechnicalSnapshot.method == TECHNICAL_METHOD,
            TechnicalSnapshot.as_of_date <= view_date,
        )
        .order_by(TechnicalSnapshot.as_of_date.desc(), TechnicalSnapshot.id.desc())
        .limit(1)
    )
    if row is None or row.technical_score is None:
        return TechnicalInput(
            score=None,
            confidence=None if row is None else row.technical_confidence,
            rsi_14=None if row is None else row.rsi_14,
            distance_sma_200_pct=None if row is None else row.distance_sma_200_pct,
            as_of_date=None if row is None else row.as_of_date,
        )
    return TechnicalInput(
        score=Decimal(row.technical_score),
        confidence=row.technical_confidence,
        rsi_14=None if row.rsi_14 is None else Decimal(row.rsi_14),
        distance_sma_200_pct=None if row.distance_sma_200_pct is None else Decimal(row.distance_sma_200_pct),
        as_of_date=row.as_of_date,
    )


async def _market(session: AsyncSession, company_id: int, view_date: date) -> MarketInput:
    price_count = await session.scalar(
        select(func.count())
        .select_from(MarketPrice)
        .where(MarketPrice.company_id == company_id, MarketPrice.trade_date <= view_date)
    )
    snapshot = await session.scalar(
        select(CompanyMarketSnapshot).where(CompanyMarketSnapshot.company_id == company_id)
    )
    has_prices = int(price_count or 0) > 0
    snapshot_on_date = (
        snapshot is not None
        and snapshot.last_market_date is not None
        and snapshot.last_market_date <= view_date
    )
    reliable = False
    cap = None
    confidence = None
    if snapshot_on_date and snapshot is not None:
        cap_on_date = snapshot.market_cap_as_of is not None and snapshot.market_cap_as_of <= view_date
        reliable = (
            snapshot.market_cap is not None
            and snapshot.market_cap_confidence in RELIABLE_CAP
            and cap_on_date
        )
        if reliable:
            cap = snapshot.market_cap
            confidence = snapshot.market_cap_confidence
    return MarketInput(
        has_market_data=has_prices or snapshot_on_date,
        market_cap=cap,
        market_cap_confidence=confidence,
        market_cap_reliable=reliable,
    )


async def _upsert(session: AsyncSession, company_id: int, view_date: date) -> tuple[InvestmentView, bool]:
    row = await session.scalar(
        select(InvestmentView).where(
            InvestmentView.company_id == company_id,
            InvestmentView.as_of_date == view_date,
            InvestmentView.method == METHOD,
        )
    )
    if row is not None:
        return row, False
    row = InvestmentView(
        company_id=company_id,
        as_of_date=view_date,
        method=METHOD,
        components_json={},
    )
    session.add(row)
    return row, True


def _apply(row: InvestmentView, assessment) -> None:
    row.quality_score = assessment.quality_score
    row.quality_confidence = assessment.quality_confidence
    row.opportunity_score = assessment.opportunity_score
    row.opportunity_confidence = assessment.opportunity_confidence
    row.opportunity_coverage = assessment.opportunity_coverage
    row.opportunity_coverage_status = assessment.opportunity_coverage_status
    row.opportunity_ranking_eligible = assessment.opportunity_ranking_eligible
    row.technical_score = assessment.technical_score
    row.technical_confidence = assessment.technical_confidence
    row.long_term_conviction_score = assessment.long_term_conviction_score
    row.entry_attractiveness_score = assessment.entry_attractiveness_score
    row.analysis_readiness_score = assessment.analysis_readiness_score
    row.long_term_conviction_label = assessment.long_term_conviction_label
    row.entry_attractiveness_label = assessment.entry_attractiveness_label
    row.analysis_readiness_label = assessment.analysis_readiness_label
    row.components_json = assessment.components
    row.updated_at = datetime.now(timezone.utc)
