"""Composite view stays three readings. Scores are local rows and jobs use Redis db 15."""

from datetime import date
from decimal import Decimal
import inspect

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.jobs import scheduler
from app.jobs.investment import schedule_investment_view_after
from app.jobs.queues import enqueue_investment_view, investment_view_job_id, redis_connection
from app.jobs import scoring as scoring_jobs
from app.jobs import technical as technical_jobs
from app.models.company import Company
from app.models.company_market_snapshot import CompanyMarketSnapshot
from app.models.company_score import CompanyScore
from app.models.investment_view import InvestmentView
from app.models.market_price import MarketPrice
from app.models.opportunity_score import OpportunityScore
from app.models.technical_snapshot import TechnicalSnapshot
from app.services.investment_view.scoring import (
    WARNING_CAP,
    WARNING_COVERAGE,
    WARNING_EXTENDED,
    WARNING_QUALITY,
    WARNING_RSI,
    WARNING_TECHNICAL,
    MarketInput,
    OpportunityInput,
    QualityInput,
    TechnicalInput,
    build_investment_view,
)
from app.services.investment_view.service import recalculate_investment_view
from app.services.market.provider import MARKET_SOURCE
from tests.test_quality_score import _headers


@pytest.fixture
def job_redis(monkeypatch):
    monkeypatch.setattr(settings, "redis_db", 15)
    connection = redis_connection()
    connection.flushdb()
    yield connection
    connection.flushdb()
    connection.close()


def _view(quality, opportunity, technical, market):
    return build_investment_view(quality, opportunity, technical, market)


def _quality(score, confidence=80, day=date(2024, 3, 1)):
    return QualityInput(score=None if score is None else Decimal(score), confidence=confidence, as_of_date=day)


def _opportunity(score, coverage, eligible, confidence=70, day=date(2024, 3, 1)):
    status = "COMPLETE" if coverage is not None and coverage >= 90 else "PARTIAL"
    if coverage is not None and coverage < 40:
        status = "INCOMPLETE"
    elif coverage is not None and coverage >= 70:
        status = "USABLE"
    return OpportunityInput(
        raw_score=None if score is None else Decimal(score),
        confidence=confidence,
        coverage=coverage,
        coverage_status=None if coverage is None else status,
        ranking_eligible=eligible,
        as_of_date=day,
    )


def _technical(score, rsi="50", distance="5", confidence=100, day=date(2024, 3, 1)):
    return TechnicalInput(
        score=None if score is None else Decimal(score),
        confidence=confidence,
        rsi_14=None if rsi is None else Decimal(rsi),
        distance_sma_200_pct=None if distance is None else Decimal(distance),
        as_of_date=day,
    )


def _market(data=True, reliable=False, cap="100"):
    return MarketInput(
        has_market_data=data,
        market_cap=None if cap is None or not reliable else Decimal(cap),
        market_cap_confidence="HIGH" if reliable else None,
        market_cap_reliable=reliable,
    )


def test_eligible_opportunity_keeps_full_weight():
    view = _view(_quality(80), _opportunity(80, 100, True), _technical(70), _market(reliable=True))
    assert view.long_term_conviction_score == Decimal("80.00")
    assert view.long_term_conviction_label == "HIGH"
    assert view.components["opportunity"]["effective_score"] == "80.00"
    assert view.components["conviction"]["quality_weight"] == "0.45"
    assert view.components["conviction"]["renormalized"] is False
    assert WARNING_COVERAGE not in view.components["warnings"]
    assert view.analysis_readiness_score == Decimal("100.00")
    assert view.analysis_readiness_label == "COMPLETE"


def test_partial_opportunity_is_not_treated_as_complete():
    view = _view(_quality(100), _opportunity(80, 45, False), _technical(70), _market())
    assert view.components["opportunity"]["effective_score"] == "36.00"
    assert view.long_term_conviction_score == Decimal("71.20")
    assert view.long_term_conviction_score != Decimal("89.00")
    assert view.long_term_conviction_label == "GOOD"
    assert view.components["conviction"]["quality_weight"] == "0.55"
    assert view.components["conviction"]["opportunity_weight"] == "0.45"
    assert WARNING_COVERAGE in view.components["warnings"]
    assert view.components["readiness"]["opportunity_coverage"] == "18.00"
    assert view.analysis_readiness_score == Decimal("73.00")
    assert view.analysis_readiness_label == "USABLE"


def test_opportunity_at_100_with_low_coverage_is_reduced():
    view = _view(_quality(100), _opportunity(100, 35, False), _technical(None), _market(data=False))
    assert view.components["opportunity"]["raw_score"] == "100.00"
    assert view.components["opportunity"]["effective_score"] == "35.00"
    assert view.long_term_conviction_score == Decimal("70.75")
    assert view.long_term_conviction_score != Decimal("100.00")
    assert WARNING_COVERAGE in view.components["warnings"]
    assert WARNING_TECHNICAL in view.components["warnings"]
    assert view.entry_attractiveness_score is None
    assert view.analysis_readiness_score == Decimal("44.00")
    assert view.analysis_readiness_label == "PARTIAL"


def test_missing_inputs_and_entry_penalties():
    empty = _view(
        _quality(None),
        _opportunity(None, None, None),
        _technical(None, rsi=None, distance=None, confidence=None),
        _market(data=False, cap=None),
    )
    assert empty.long_term_conviction_score is None
    assert empty.long_term_conviction_label is None
    assert empty.entry_attractiveness_score is None
    assert empty.analysis_readiness_score == Decimal("0.00")
    assert empty.analysis_readiness_label == "INCOMPLETE"
    assert WARNING_QUALITY in empty.components["warnings"]
    assert WARNING_CAP in empty.components["warnings"]

    extended = _view(_quality(90), _opportunity(80, 90, True), _technical(88, rsi="85", distance="40"), _market())
    calm = _view(_quality(20), _opportunity(80, 90, True), _technical(88, rsi="50", distance="5"), _market())
    assert extended.entry_attractiveness_score == Decimal("75.00")
    assert extended.entry_attractiveness_label == "FAVORABLE"
    assert calm.entry_attractiveness_score == Decimal("88.00")
    assert calm.entry_attractiveness_label == "STRONG"
    assert extended.entry_attractiveness_score != extended.quality_score
    assert WARNING_RSI in extended.components["warnings"]
    assert WARNING_EXTENDED in extended.components["warnings"]
    assert "Quality is not used for timing" in extended.components["technical"]["note"]

    very_high = _view(_quality(90), _opportunity(80, 90, True), _technical(100, rsi="50", distance="5"), _market())
    assert very_high.entry_attractiveness_label == "EXTENDED_OR_EXCEPTIONAL"
    low_confidence = _view(
        _quality(90),
        _opportunity(80, 90, True),
        _technical(80, confidence=60),
        _market(),
    )
    assert low_confidence.entry_attractiveness_score == Decimal("75.00")
    assert low_confidence.components["technical"]["penalty"] == "5.00"


def test_labels_follow_the_published_bands():
    assert _view(_quality(39), _opportunity(39, 100, True), _technical(39), _market()).long_term_conviction_label == "LOW"
    moderate = _view(_quality(50), _opportunity(50, 100, True), _technical(50), _market(data=False, cap=None))
    assert moderate.long_term_conviction_label == "MODERATE"
    assert moderate.entry_attractiveness_label == "NEUTRAL"
    assert moderate.analysis_readiness_score == Decimal("85.00")
    assert moderate.analysis_readiness_label == "USABLE"
    high = _view(_quality(100), _opportunity(100, 100, True), _technical(75), _market(reliable=True))
    assert high.long_term_conviction_label == "VERY_HIGH"
    assert high.entry_attractiveness_label == "STRONG"
    assert high.analysis_readiness_label == "COMPLETE"


def test_view_jobs_follow_score_jobs_without_a_scheduler(job_redis):
    calls = []

    def _enqueue(company_id):
        calls.append(company_id)
        return {"enqueued": True, "status": "queued"}

    first = enqueue_investment_view(9)
    second = enqueue_investment_view(9)
    assert first["job_id"] == investment_view_job_id(9) == "investment-view-company-9"
    assert first["queue"] == "analysis"
    assert first["enqueued"] is True
    assert second["enqueued"] is False
    assert second["status"] in {"queued", "started", "deferred", "scheduled"}

    # The duplicate check above uses the real queue. The schedule checks use a fake enqueue.
    import app.jobs.investment as investment_jobs

    investment_jobs.enqueue_investment_view = _enqueue
    try:
        assert schedule_investment_view_after({"status": "success", "company_id": 4}) is True
        assert schedule_investment_view_after({"status": "insufficient_history", "company_id": 4}) is True
        assert schedule_investment_view_after({"status": "SKIPPED_NO_PROFILE", "company_id": 4}) is False
        assert schedule_investment_view_after({"status": "no_history", "company_id": 4}) is False
        assert schedule_investment_view_after({"status": "missing_company"}) is False
    finally:
        investment_jobs.enqueue_investment_view = enqueue_investment_view
    assert calls == [4, 4]
    assert "schedule_investment_view_after" in inspect.getsource(scoring_jobs)
    assert "schedule_investment_view_after" in inspect.getsource(technical_jobs.recalculate_technical)
    assert "enqueue_investment_view" not in inspect.getsource(scheduler)


async def test_history_updates_same_day_and_ignores_future_scores(session_factory):
    async with session_factory() as session:
        company = Company(name="View History", ticker="VHIST")
        session.add(company)
        await session.commit()
        await session.refresh(company)
        _add_inputs(session, company.id, date(2024, 3, 1), quality="90", opportunity="100", coverage=80, eligible=True, technical="70")
        _add_inputs(session, company.id, date(2024, 6, 1), quality="10", opportunity="10", coverage=100, eligible=True, technical="10", rsi="90", distance="60", confidence=40)
        session.add(
            CompanyMarketSnapshot(
                company_id=company.id,
                market_cap=Decimal("1000.00"),
                market_cap_confidence="HIGH",
                market_cap_as_of=date(2024, 6, 1),
                last_market_date=date(2024, 6, 1),
                source=MARKET_SOURCE,
            )
        )
        await session.commit()
        early = await recalculate_investment_view(session, company.id, date(2024, 4, 1))
        early_conviction = early.long_term_conviction_score
        early_quality = early.quality_score
        early_id = early.id
        later = await recalculate_investment_view(session, company.id, date(2024, 6, 1))
        later_conviction = later.long_term_conviction_score
        quality = await session.scalar(
            select(CompanyScore).where(CompanyScore.company_id == company.id, CompanyScore.score_date == date(2024, 3, 1))
        )
        quality.quality_score = Decimal("80.00")
        await session.commit()
        updated = await recalculate_investment_view(session, company.id, date(2024, 4, 1))
        await session.refresh(later)
        count = await session.scalar(
            select(func.count()).select_from(InvestmentView).where(InvestmentView.company_id == company.id)
        )

    assert early_quality == Decimal("90.00")
    assert early.components_json["opportunity"]["effective_score"] == "100.00"
    assert early_conviction == Decimal("95.50")
    assert early.technical_score == Decimal("70.00")
    assert WARNING_CAP in early.components_json["warnings"]
    assert later.quality_score == Decimal("10.00")
    assert later_conviction == Decimal("10.00")
    assert later.analysis_readiness_label == "COMPLETE"
    assert updated.id == early_id
    assert updated.long_term_conviction_score == Decimal("91.00")
    assert later.long_term_conviction_score == Decimal("10.00")
    assert count == 2


async def test_investment_routes_history_filters_jwt_and_admin(client, session_factory, job_redis):
    assert (await client.post("/companies/1/investment-view/recalculate")).status_code == 401
    assert (await client.get("/companies/1/investment-view/latest")).status_code == 401
    assert (await client.get("/companies/1/investment-view/history")).status_code == 401
    headers = await _headers(client, email="view@example.com")
    created = await client.post("/companies", json={"name": "View Route", "ticker": "VROUTE"}, headers=headers)
    company_id = created.json()["id"]
    async with session_factory() as session:
        _add_inputs(session, company_id, date(2024, 1, 2), quality="60", opportunity="60", coverage=80, eligible=True, technical="60")
        _add_inputs(session, company_id, date(2024, 2, 2), quality="70", opportunity="70", coverage=80, eligible=True, technical="70")
        await session.commit()
        await recalculate_investment_view(session, company_id, date(2024, 1, 2))
        await recalculate_investment_view(session, company_id, date(2024, 2, 2))
        session.add(
            InvestmentView(
                company_id=company_id,
                as_of_date=date(2024, 2, 2),
                method="investment_view_v0",
                analysis_readiness_score=Decimal("0"),
                analysis_readiness_label="INCOMPLETE",
                components_json={"warnings": []},
            )
        )
        await session.commit()
    latest = await client.get(f"/companies/{company_id}/investment-view/latest", headers=headers)
    history = await client.get(f"/companies/{company_id}/investment-view/history", headers=headers)
    window = await client.get(
        f"/companies/{company_id}/investment-view/history",
        params={"date_from": "2024-01-01", "date_to": "2024-01-31"},
        headers=headers,
    )
    other = await client.get(
        f"/companies/{company_id}/investment-view/history",
        params={"method": "investment_view_v0"},
        headers=headers,
    )
    admin = await client.get("/admin/status", headers=headers)
    dates = [row["as_of_date"] for row in history.json()]

    assert latest.status_code == 200
    assert latest.json()["as_of_date"] == "2024-02-02"
    assert latest.json()["method"] == "investment_view_v1"
    assert "buy" not in latest.json()
    assert dates == ["2024-02-02", "2024-01-02"]
    assert [row["as_of_date"] for row in window.json()] == ["2024-01-02"]
    assert len(other.json()) == 1
    assert other.json()[0]["method"] == "investment_view_v0"
    assert admin.json()["investment_view"]["companies_with_view"] == 1
    assert admin.json()["investment_view"]["last_calculation"]


def _add_inputs(
    session,
    company_id: int,
    day: date,
    *,
    quality: str,
    opportunity: str,
    coverage: int,
    eligible: bool,
    technical: str,
    rsi: str = "50",
    distance: str = "5",
    confidence: int = 100,
) -> None:
    session.add(
        CompanyScore(
            company_id=company_id,
            score_date=day,
            quality_score=Decimal(quality),
            overall_confidence_score=80,
            metrics_json={},
            confidence_json={},
            method_version="quality_v1",
        )
    )
    session.add(
        OpportunityScore(
            company_id=company_id,
            score_date=day,
            opportunity_score=Decimal(opportunity),
            opportunity_confidence_score=70,
            coverage_score=coverage,
            coverage_status="USABLE" if coverage >= 70 else "PARTIAL",
            ranking_eligible=eligible,
            components_json={},
            confidence_json={},
            method_version="opportunity_v1",
        )
    )
    session.add(
        TechnicalSnapshot(
            company_id=company_id,
            as_of_date=day,
            method="technical_v1",
            technical_score=Decimal(technical),
            technical_confidence=confidence,
            rsi_14=Decimal(rsi),
            distance_sma_200_pct=Decimal(distance),
            components_json={},
        )
    )
    session.add(
        MarketPrice(
            company_id=company_id,
            trade_date=day,
            close=Decimal("10"),
            adjusted_close=Decimal("10"),
            source=MARKET_SOURCE,
        )
    )
