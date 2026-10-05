from decimal import Decimal

from app.services.analysis.annual_metrics import AnnualSnapshot, build_annual_metrics
from app.services.analysis.growth_runway import growth_runway_score, rd_intensity_score, revenue_growth_acceleration
from app.services.analysis.opportunity_score import (
    MegatrendInput,
    OpportunityInputs,
    build_opportunity_score,
    classify_coverage,
)
from app.services.analysis.opportunity_thresholds import WEIGHTS
from app.services.analysis.size_runway import size_runway_score
from tests.test_quality_score import _headers


def _growing(rate: Decimal) -> list[AnnualSnapshot]:
    rows = []
    revenue = Decimal("100")
    income = Decimal("20")
    fcf = Decimal("10")
    for offset in range(6):
        rows.append(
            AnnualSnapshot(
                fiscal_year=2020 + offset,
                revenue=revenue,
                operating_income=income,
                net_income=income,
                free_cash_flow=fcf,
            )
        )
        revenue *= Decimal("1") + rate
        income *= Decimal("1") + rate
        fcf *= Decimal("1") + rate
    return rows


def _flat() -> list[AnnualSnapshot]:
    return [
        AnnualSnapshot(
            fiscal_year=2020 + offset,
            revenue=Decimal("100"),
            operating_income=Decimal("10"),
            net_income=Decimal("10"),
            free_cash_flow=Decimal("5"),
        )
        for offset in range(6)
    ]


def _stalling() -> list[AnnualSnapshot]:
    revenues = [
        Decimal("100"),
        Decimal("170"),
        Decimal("289"),
        Decimal("491"),
        Decimal("835"),
        Decimal("840"),
    ]
    return [
        AnnualSnapshot(
            fiscal_year=2020 + offset,
            revenue=revenue,
            operating_income=revenue * Decimal("0.2"),
            net_income=revenue * Decimal("0.2"),
            free_cash_flow=revenue * Decimal("0.1"),
        )
        for offset, revenue in enumerate(revenues)
    ]


def _score(snapshots, **kwargs):
    return build_opportunity_score(
        OpportunityInputs(snapshots=snapshots, metrics=build_annual_metrics(snapshots), **kwargs)
    )


def test_size_runway_bands():
    assert size_runway_score(Decimal("500000000")).score == Decimal("80")
    assert size_runway_score(Decimal("2000000000")).score == Decimal("100")
    assert size_runway_score(Decimal("10000000000")).score == Decimal("85")
    assert size_runway_score(Decimal("50000000000")).score == Decimal("70")
    assert size_runway_score(Decimal("200000000000")).score == Decimal("50")
    assert size_runway_score(Decimal("1000000000000")).score == Decimal("30")
    assert size_runway_score(Decimal("2000000000000")).score == Decimal("15")
    assert size_runway_score(None).score is None
    assert size_runway_score(Decimal("0")).score is None


def test_size_alone_does_not_produce_an_opportunity_score():
    assessment = build_opportunity_score(
        OpportunityInputs(
            snapshots=[],
            metrics=build_annual_metrics([]),
            market_cap=Decimal("2000000000"),
        )
    )
    assert assessment.opportunity_score is None
    assert assessment.components["size_runway"].score == Decimal("100.00")
    assert assessment.components["size_runway"].included is False
    assert assessment.opportunity_confidence_score == 0


def test_small_company_with_no_growth_does_not_score_well():
    assessment = _score(_flat(), market_cap=Decimal("2000000000"))
    assert assessment.components["growth_runway"].score == Decimal("0.00")
    assert assessment.components["size_runway"].included is True
    assert assessment.opportunity_score is not None
    assert assessment.opportunity_score < Decimal("50")


def test_growth_runway_stays_high_when_growth_persists():
    snapshots = _growing(Decimal("0.35"))
    metrics = build_annual_metrics(snapshots)
    growth = growth_runway_score(snapshots, metrics)
    assert growth.score == Decimal("100.00")
    assert revenue_growth_acceleration(metrics) == Decimal("0.00")


def test_growth_runway_penalizes_deceleration():
    steady = growth_runway_score(_growing(Decimal("0.35")), build_annual_metrics(_growing(Decimal("0.35"))))
    stalling = _stalling()
    slowed = growth_runway_score(stalling, build_annual_metrics(stalling))
    assert slowed.acceleration is not None
    assert slowed.acceleration < 0
    assert slowed.score is not None
    assert steady.score is not None
    assert slowed.score < steady.score - Decimal("20")


def test_missing_components_are_excluded_and_weights_are_renormalized():
    snapshots = _growing(Decimal("0.35"))
    assessment = _score(
        snapshots,
        megatrends=(
            MegatrendInput(trend="AI", exposure_score=Decimal("100"), confidence=80),
        ),
    )
    assert assessment.components["bottleneck"].score is None
    assert assessment.components["bottleneck"].included is False
    assert "bottleneck" in assessment.excluded
    included_weight = sum(
        WEIGHTS[name] for name, item in assessment.components.items() if item.included
    )
    assert assessment.available_weight == included_weight
    assert assessment.available_weight < sum(WEIGHTS.values())
    earned = sum(item.points for item in assessment.components.values() if item.included and item.points is not None)
    expected = (earned / assessment.available_weight * Decimal(100)).quantize(Decimal("0.01"))
    assert assessment.opportunity_score == expected
    assert assessment.opportunity_confidence_score < 100


def test_megatrend_confidence_uses_the_manual_source_cap():
    assessment = build_opportunity_score(
        OpportunityInputs(
            snapshots=[],
            metrics=build_annual_metrics([]),
            megatrends=(
                MegatrendInput(
                    trend="AI",
                    exposure_score=Decimal("100"),
                    confidence=95,
                    source="MANUAL_STRUCTURED",
                ),
            ),
        )
    )
    assert assessment.components["megatrend"].score == Decimal("100.00")
    assert assessment.components["megatrend"].confidence == 80
    assert assessment.components["megatrend"].source == "MANUAL_STRUCTURED"
    assert assessment.opportunity_score == Decimal("100.00")
    assert assessment.available_weight == WEIGHTS["megatrend"]


def test_rd_intensity_is_null_without_revenue():
    assert rd_intensity_score(Decimal("20"), Decimal("100")) == Decimal("100.00")
    assert rd_intensity_score(Decimal("20"), None) is None
    assert rd_intensity_score(None, Decimal("100")) is None


def test_weights_sum_to_100():
    assert sum(WEIGHTS.values()) == Decimal("100")


def test_coverage_status_bands_and_ranking_eligibility():
    assert classify_coverage(Decimal("35")) == (35, "INCOMPLETE", False)
    assert classify_coverage(Decimal("50")) == (50, "PARTIAL", False)
    assert classify_coverage(Decimal("69")) == (69, "PARTIAL", False)
    assert classify_coverage(Decimal("70")) == (70, "USABLE", True)
    assert classify_coverage(Decimal("75")) == (75, "USABLE", True)
    assert classify_coverage(Decimal("95")) == (95, "COMPLETE", True)
    assert classify_coverage(Decimal("100")) == (100, "COMPLETE", True)


def test_incomplete_coverage_keeps_the_renormalized_score_out_of_ranking():
    snapshots = _growing(Decimal("0.35"))
    assessment = _score(
        snapshots,
        megatrends=(MegatrendInput(trend="AI", exposure_score=Decimal("100"), confidence=80),),
    )
    assert assessment.opportunity_score == Decimal("100.00")
    assert assessment.coverage_score == 35
    assert assessment.coverage_status == "INCOMPLETE"
    assert assessment.ranking_eligible is False
    assert assessment.available_weight == Decimal("35")


async def test_profile_put_get_recalculate_and_summary(client):
    headers = await _headers(client, email="opportunity@example.com")
    created = await client.post(
        "/companies",
        json={"name": "Opportunity Corp", "ticker": "OPPR", "market_cap": "2000000000000"},
        headers=headers,
    )
    company_id = created.json()["id"]
    missing = await client.get(f"/companies/{company_id}/opportunity-profile", headers=headers)
    rejected = await client.put(
        f"/companies/{company_id}/opportunity-profile",
        json={"market_growth_score": 101},
        headers=headers,
    )
    unknown = await client.put(
        f"/companies/{company_id}/opportunity-profile",
        json={"megatrends_json": [{"trend": "NotATrend", "exposure_score": 10, "confidence": 10}]},
        headers=headers,
    )
    cleared = await client.put(
        f"/companies/{company_id}/opportunity-profile",
        json={"market_growth_score": None, "bottleneck_score": None},
        headers=headers,
    )
    saved = await client.put(
        f"/companies/{company_id}/opportunity-profile",
        json={
            "megatrends_json": [
                {
                    "trend": "AI",
                    "exposure_score": 100,
                    "confidence": 80,
                    "evidence": "Development fixture only.",
                    "source": "MANUAL_STRUCTURED",
                }
            ],
            "strategic_roles_json": ["CRITICAL_SUPPLIER"],
            "market_growth_score": None,
        },
        headers=headers,
    )
    loaded = await client.get(f"/companies/{company_id}/opportunity-profile", headers=headers)
    first = await client.post(f"/companies/{company_id}/scores/opportunity/recalculate", headers=headers)
    second = await client.post(f"/companies/{company_id}/scores/opportunity/recalculate", headers=headers)
    latest = await client.get(f"/companies/{company_id}/scores/opportunity/latest", headers=headers)
    history = await client.get(f"/companies/{company_id}/scores/opportunity/history", headers=headers)
    summary = await client.get(f"/companies/{company_id}/analysis-summary", headers=headers)

    assert missing.status_code == 404
    assert rejected.status_code == 422
    assert unknown.status_code == 422
    assert cleared.status_code == 200
    assert cleared.json()["market_growth_score"] is None
    assert saved.status_code == 200
    assert saved.json()["megatrends_json"][0]["trend"] == "AI"
    assert saved.json()["strategic_roles_json"] == ["CRITICAL_SUPPLIER"]
    assert loaded.json()["method_version"] == "opportunity_v1"
    assert first.status_code == 200
    body = first.json()
    assert body["opportunity_score"] == "66.00"
    assert body["components"]["size_runway"]["score"] == "15.00"
    assert body["components"]["size_runway"]["included"] is True
    assert body["components"]["megatrend"]["included"] is True
    assert body["components"]["strategic_position"]["score"] is None
    assert body["components"]["growth_runway"]["included"] is False
    assert Decimal(body["available_weight"]) == Decimal("25")
    assert "strategic_position" in body["excluded"]
    assert "buy" not in " ".join(body["notes"]).lower()
    assert second.json()["id"] == body["id"]
    assert latest.json()["opportunity_score"] == body["opportunity_score"]
    assert len(history.json()) == 1
    assert summary.status_code == 200
    assert summary.json()["quality"]["score"] is None
    assert summary.json()["opportunity"]["score"] == "66.00"
    assert summary.json()["opportunity"]["method_version"] == "opportunity_v1"
    assert summary.json()["company"]["ticker"] == "OPPR"


async def test_opportunity_routes_require_jwt(client):
    profile = await client.get("/companies/1/opportunity-profile")
    updated = await client.put("/companies/1/opportunity-profile", json={})
    recalculated = await client.post("/companies/1/scores/opportunity/recalculate")
    latest = await client.get("/companies/1/scores/opportunity/latest")
    history = await client.get("/companies/1/scores/opportunity/history")
    summary = await client.get("/companies/1/analysis-summary")
    assert profile.status_code == 401
    assert updated.status_code == 401
    assert recalculated.status_code == 401
    assert latest.status_code == 401
    assert history.status_code == 401
    assert summary.status_code == 401
