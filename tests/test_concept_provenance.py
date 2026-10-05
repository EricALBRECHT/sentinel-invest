from dataclasses import replace
from decimal import Decimal

from app.models.financial_metric import FinancialMetric
from app.services.analysis.annual_metrics import build_annual_metrics
from app.services.analysis.confidence import build_confidence
from app.services.analysis.quality_score import build_quality_score
from app.services.sec.company_facts import parse_company_facts, sourced_value
from app.services.sec.mappings import (
    DEBT_COMPREHENSIVE_CONCEPT,
    OPERATING_CASH_FLOW_CONCEPT,
)
from tests.sec_fixtures import duration, facts_payload, instant
from tests.test_quality_score import _headers, _steady_history, _stored_year

EXACT_CAPEX = "PaymentsToAcquirePropertyPlantAndEquipment"
FALLBACK_CAPEX = "PaymentsToAcquireProductiveAssets"
OTHER_EXACT_CAPEX = "PaymentsForAdditionsToPropertyPlantAndEquipment"


def test_revenue_provenance_keeps_the_selected_concept():
    payload = facts_payload(
        (
            "us-gaap",
            "Revenues",
            "USD",
            [duration("2023-01-30", "2024-01-28", 80, fy=2024, fp="FY")],
        ),
    )
    period = parse_company_facts(payload, cik="1045810").periods[0]
    sourced = sourced_value(period, "revenue")
    assert period.revenue_source_concept == "Revenues"
    assert sourced is not None
    assert sourced.value == Decimal("80")
    assert sourced.concept == "Revenues"


def test_capex_provenance_keeps_the_exact_and_fallback_concepts():
    exact = facts_payload(
        (
            "us-gaap",
            OPERATING_CASH_FLOW_CONCEPT,
            "USD",
            [duration("2023-01-30", "2024-01-28", 100, fy=2024, fp="FY")],
        ),
        (
            "us-gaap",
            EXACT_CAPEX,
            "USD",
            [duration("2023-01-30", "2024-01-28", 40, fy=2024, fp="FY")],
        ),
    )
    fallback = facts_payload(
        (
            "us-gaap",
            OPERATING_CASH_FLOW_CONCEPT,
            "USD",
            [duration("2023-01-30", "2024-01-28", 100, fy=2024, fp="FY")],
        ),
        (
            "us-gaap",
            FALLBACK_CAPEX,
            "USD",
            [duration("2023-01-30", "2024-01-28", 40, fy=2024, fp="FY")],
        ),
    )
    exact_period = parse_company_facts(exact, cik="1045810").periods[0]
    fallback_period = parse_company_facts(fallback, cik="1045810").periods[0]
    assert exact_period.capital_expenditure_source_concept == EXACT_CAPEX
    assert exact_period.free_cash_flow == Decimal("60")
    assert fallback_period.capital_expenditure_source_concept == FALLBACK_CAPEX
    assert sourced_value(fallback_period, "capital_expenditure").concept == FALLBACK_CAPEX


def test_debt_provenance_keeps_comprehensive_and_reconstructed_concepts():
    comprehensive = facts_payload(
        (
            "us-gaap",
            "Revenues",
            "USD",
            [duration("2023-01-30", "2024-01-28", 10, fy=2024, fp="FY")],
        ),
        (
            "us-gaap",
            DEBT_COMPREHENSIVE_CONCEPT,
            "USD",
            [instant("2024-01-28", 100, fy=2024, fp="FY")],
        ),
    )
    reconstructed = facts_payload(
        (
            "us-gaap",
            "Revenues",
            "USD",
            [duration("2023-01-30", "2024-01-28", 10, fy=2024, fp="FY")],
        ),
        (
            "us-gaap",
            "LongTermDebtNoncurrent",
            "USD",
            [instant("2024-01-28", 80, fy=2024, fp="FY")],
        ),
        (
            "us-gaap",
            "LongTermDebtCurrent",
            "USD",
            [instant("2024-01-28", 20, fy=2024, fp="FY")],
        ),
    )
    comprehensive_period = parse_company_facts(comprehensive, cik="1045810").periods[0]
    reconstructed_period = parse_company_facts(reconstructed, cik="1045810").periods[0]
    assert comprehensive_period.debt_source_concept == DEBT_COMPREHENSIVE_CONCEPT
    assert sourced_value(comprehensive_period, "total_debt").concept == DEBT_COMPREHENSIVE_CONCEPT
    assert reconstructed_period.total_debt == Decimal("100")
    assert reconstructed_period.debt_source_concept == "LongTermDebtNoncurrent+LongTermDebtCurrent"


def _with_sources(periods, **concepts):
    return [replace(period, **concepts) for period in periods]


def test_fcf_confidence_is_high_for_an_exact_ppe_concept():
    for concept in (EXACT_CAPEX, OTHER_EXACT_CAPEX):
        periods = _with_sources(
            _steady_history(),
            operating_cash_flow_source_concept=OPERATING_CASH_FLOW_CONCEPT,
            capital_expenditure_source_concept=concept,
        )
        confidence = build_confidence(periods, build_annual_metrics(periods))
        assert confidence.fcf_confidence == "HIGH"
        assert confidence.fcf.score == 100
        assert confidence.fcf.mixed_source_concepts is False
        assert concept in confidence.fcf.source_concepts


def test_fcf_confidence_is_medium_for_productive_assets_fallback():
    periods = _with_sources(
        _steady_history(),
        operating_cash_flow_source_concept=OPERATING_CASH_FLOW_CONCEPT,
        capital_expenditure_source_concept=FALLBACK_CAPEX,
    )
    confidence = build_confidence(periods, build_annual_metrics(periods))
    assert confidence.fcf.level == "MEDIUM"
    assert confidence.fcf.score == 70
    assert FALLBACK_CAPEX in confidence.fcf.reason
    assert confidence.fcf.source_concepts == (OPERATING_CASH_FLOW_CONCEPT, FALLBACK_CAPEX)


def test_fcf_confidence_is_medium_when_capex_concepts_are_mixed():
    periods = []
    for index, period in enumerate(_steady_history()):
        concept = EXACT_CAPEX if index < 3 else FALLBACK_CAPEX
        periods.append(
            replace(
                period,
                operating_cash_flow_source_concept=OPERATING_CASH_FLOW_CONCEPT,
                capital_expenditure_source_concept=concept,
            )
        )
    confidence = build_confidence(periods, build_annual_metrics(periods))
    assert confidence.fcf.level == "MEDIUM"
    assert confidence.fcf.mixed_source_concepts is True
    assert EXACT_CAPEX in confidence.fcf.source_concepts
    assert FALLBACK_CAPEX in confidence.fcf.source_concepts


def test_debt_confidence_is_high_for_the_comprehensive_concept():
    periods = _with_sources(_steady_history(), debt_source_concept=DEBT_COMPREHENSIVE_CONCEPT)
    confidence = build_confidence(periods, build_annual_metrics(periods))
    assert confidence.debt.level == "HIGH"
    assert confidence.debt.score == 100
    assert confidence.debt.source_concepts == (DEBT_COMPREHENSIVE_CONCEPT,)
    assert confidence.debt.mixed_source_concepts is False


def test_debt_confidence_is_medium_for_the_long_term_debt_fallback():
    periods = _with_sources(_steady_history(), debt_source_concept="LongTermDebt")
    confidence = build_confidence(periods, build_annual_metrics(periods))
    assert confidence.debt.level == "MEDIUM"
    assert confidence.debt.score == 70
    assert "LongTermDebt" in confidence.debt.reason
    assert confidence.debt.source_concepts == ("LongTermDebt",)


def test_share_fallback_concept_is_low_confidence():
    periods = _with_sources(
        _steady_history(),
        shares_source_concept="EntityCommonStockSharesOutstanding",
    )
    confidence = build_confidence(periods, build_annual_metrics(periods))
    assert confidence.shares.level == "LOW"
    assert "EntityCommonStockSharesOutstanding" in confidence.shares.reason


def test_source_concepts_do_not_change_the_quality_score():
    bare = _steady_history()
    tagged = _with_sources(
        bare,
        revenue_source_concept="Revenues",
        operating_cash_flow_source_concept=OPERATING_CASH_FLOW_CONCEPT,
        capital_expenditure_source_concept=FALLBACK_CAPEX,
        debt_source_concept="LongTermDebt",
        shares_source_concept="CommonStockSharesOutstanding",
    )
    bare_score = build_quality_score(bare, build_annual_metrics(bare))
    tagged_score = build_quality_score(tagged, build_annual_metrics(tagged))
    assert tagged_score.quality_score == bare_score.quality_score
    assert tagged_score.components == bare_score.components


async def test_confidence_json_keeps_reason_and_concepts(client, session_factory):
    headers = await _headers(client, email="provenance@example.com")
    created = await client.post(
        "/companies",
        json={"name": "Provenance Corp", "ticker": "PROV"},
        headers=headers,
    )
    company_id = created.json()["id"]
    async with session_factory() as session:
        rows = []
        for year in range(2021, 2027):
            row = _stored_year(company_id, year)
            row.revenue_source_concept = "Revenues"
            row.net_income_source_concept = "NetIncomeLoss"
            row.operating_cash_flow_source_concept = OPERATING_CASH_FLOW_CONCEPT
            row.capital_expenditure_source_concept = FALLBACK_CAPEX
            row.debt_source_concept = "LongTermDebt"
            row.shares_source_concept = "CommonStockSharesOutstanding"
            rows.append(row)
        session.add_all(rows)
        await session.commit()

    response = await client.post(
        f"/companies/{company_id}/scores/quality/recalculate",
        headers=headers,
    )
    latest = await client.get(f"/companies/{company_id}/scores/latest", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["quality_score"] is not None
    assert body["confidence"]["fcf"]["level"] == "MEDIUM"
    assert body["confidence"]["fcf"]["score"] == 70
    assert body["confidence"]["fcf"]["source_concepts"] == [
        OPERATING_CASH_FLOW_CONCEPT,
        FALLBACK_CAPEX,
    ]
    assert "PaymentsToAcquireProductiveAssets" in body["confidence"]["fcf"]["reason"]
    assert body["confidence"]["debt"]["level"] == "MEDIUM"
    assert body["confidence"]["debt"]["source_concepts"] == ["LongTermDebt"]
    assert body["confidence"]["revenue"]["source_concepts"] == ["Revenues"]
    assert latest.json()["confidence"]["fcf"]["reason"] == body["confidence"]["fcf"]["reason"]
    assert isinstance(rows[0], FinancialMetric)


async def test_score_routes_stay_protected_without_jwt(client):
    recalculated = await client.post("/companies/1/scores/quality/recalculate")
    latest = await client.get("/companies/1/scores/latest")
    assert recalculated.status_code == 401
    assert latest.status_code == 401
