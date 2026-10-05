from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models.company_score import CompanyScore
from app.models.financial_metric import FinancialMetric
from app.services.analysis.annual_metrics import AnnualSnapshot, build_annual_metrics, cagr, growth_rate, ratio
from app.services.analysis.confidence import build_confidence, confidence_from_count
from app.services.analysis.quality_score import build_quality_score
from app.services.analysis.thresholds import METHOD_VERSION, WEIGHTS


def _year(year: int, **values) -> AnnualSnapshot:
    return AnnualSnapshot(fiscal_year=year, **values)


def _steady_history() -> list[AnnualSnapshot]:
    """Six positive annual years, no share split, so every component can be scored."""
    rows = []
    revenue = Decimal("100")
    income = Decimal("10")
    fcf = Decimal("8")
    equity = Decimal("80")
    assets = Decimal("160")
    debt = Decimal("20")
    cash = Decimal("30")
    shares = Decimal("1000")
    for offset in range(6):
        rows.append(
            _year(
                2020 + offset,
                revenue=revenue,
                gross_profit=revenue * Decimal("0.4"),
                operating_income=income,
                net_income=income,
                free_cash_flow=fcf,
                shareholders_equity=equity,
                total_assets=assets,
                total_debt=debt,
                cash_and_equivalents=cash,
                shares_outstanding=shares,
            )
        )
        revenue *= Decimal("1.12")
        income *= Decimal("1.12")
        fcf *= Decimal("1.12")
        equity *= Decimal("1.05")
        assets *= Decimal("1.05")
        shares *= Decimal("0.99")
    return rows


def test_cagr_compounds_a_positive_series():
    rate = cagr(Decimal("100"), Decimal("121"), 2)
    assert rate == Decimal("0.10000000")


def test_cagr_is_null_when_the_initial_value_is_not_positive():
    assert cagr(Decimal("-10"), Decimal("20"), 3) is None
    assert cagr(Decimal("0"), Decimal("20"), 3) is None
    assert cagr(Decimal("20"), Decimal("-5"), 3) is None
    assert growth_rate(Decimal("-10"), Decimal("5")) is None


def test_margin_roe_roa_and_debt_to_fcf():
    latest = _year(
        2026,
        revenue=Decimal("100"),
        gross_profit=Decimal("40"),
        operating_income=Decimal("20"),
        net_income=Decimal("15"),
        free_cash_flow=Decimal("10"),
        shareholders_equity=Decimal("100"),
        total_assets=Decimal("200"),
        total_debt=Decimal("20"),
    )
    metrics = build_annual_metrics([latest])
    assert metrics.gross_margin == Decimal("0.40000000")
    assert metrics.operating_margin == Decimal("0.20000000")
    assert metrics.roe == Decimal("0.15000000")
    assert metrics.roa == Decimal("0.07500000")
    assert metrics.debt_to_fcf == Decimal("2.00000000")


def test_division_by_zero_returns_null():
    assert ratio(Decimal("10"), Decimal("0")) is None
    assert ratio(Decimal("10"), None) is None
    metrics = build_annual_metrics(
        [
            _year(
                2026,
                revenue=Decimal("0"),
                operating_income=Decimal("5"),
                net_income=Decimal("5"),
                free_cash_flow=Decimal("0"),
                shareholders_equity=Decimal("0"),
                total_assets=Decimal("0"),
                total_debt=Decimal("0"),
            )
        ]
    )
    assert metrics.operating_margin is None
    assert metrics.roe is None
    assert metrics.roa is None
    assert metrics.debt_to_fcf is None
    assert metrics.cash_to_debt is None


def test_ten_for_one_split_marks_shares_not_comparable():
    periods = [
        _year(2023, revenue=Decimal("10"), shares_outstanding=Decimal("100")),
        _year(2024, revenue=Decimal("12"), shares_outstanding=Decimal("1000")),
    ]
    metrics = build_annual_metrics(periods)
    assert metrics.shares_comparable is False
    assert metrics.share_splits[0].factor == 10
    assert metrics.shares_cagr_3y is None
    assert metrics.shares_growth_1y is None
    score = build_quality_score(periods, metrics)
    assert score.components["dilution"] is None


def test_confidence_levels():
    assert confidence_from_count(0) == "MISSING"
    assert confidence_from_count(2) == "LOW"
    assert confidence_from_count(4) == "MEDIUM"
    assert confidence_from_count(5) == "HIGH"
    assert confidence_from_count(6, xbrl_fallback=True) == "MEDIUM"
    assert confidence_from_count(6, comparability_issue=True) == "LOW"


def test_quality_score_uses_every_component_when_data_is_complete():
    periods = _steady_history()
    metrics = build_annual_metrics(periods)
    score = build_quality_score(periods, metrics)
    assert metrics.shares_comparable is True
    assert score.quality_score is not None
    assert all(points is not None for points in score.components.values())
    assert score.available_weight == Decimal("100")
    assert Decimal("0") <= score.quality_score <= Decimal("100")


def test_missing_component_is_excluded_and_weights_are_renormalized():
    periods = [
        _year(
            2024,
            revenue=Decimal("100"),
            operating_income=Decimal("20"),
            net_income=Decimal("15"),
            free_cash_flow=Decimal("10"),
            shareholders_equity=Decimal("80"),
            total_assets=Decimal("160"),
            total_debt=Decimal("20"),
            cash_and_equivalents=Decimal("10"),
            shares_outstanding=Decimal("100"),
        ),
        _year(
            2025,
            revenue=Decimal("130"),
            operating_income=Decimal("30"),
            net_income=Decimal("22"),
            free_cash_flow=Decimal("18"),
            shareholders_equity=Decimal("90"),
            total_assets=Decimal("180"),
            total_debt=Decimal("18"),
            cash_and_equivalents=Decimal("12"),
            shares_outstanding=Decimal("1000"),
        ),
    ]
    metrics = build_annual_metrics(periods)
    score = build_quality_score(periods, metrics)
    assert score.components["dilution"] is None
    assert score.available_weight == sum(WEIGHTS.values()) - WEIGHTS["dilution"]
    earned = sum(points for points in score.components.values() if points is not None)
    expected = (earned / score.available_weight * Decimal(100)).quantize(Decimal("0.01"))
    assert score.quality_score == expected
    assert score.quality_score != earned


def test_weights_sum_to_100():
    assert sum(WEIGHTS.values()) == Decimal("100")


async def _headers(client, email="score@example.com"):
    registered = await client.post(
        "/auth/register",
        json={"email": email, "password": "password123"},
    )
    assert registered.status_code == 201
    login = await client.post(
        "/auth/login",
        data={"username": email, "password": "password123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _stored_year(company_id: int, year: int) -> FinancialMetric:
    revenue = Decimal(100 + year)
    return FinancialMetric(
        company_id=company_id,
        fiscal_year=year,
        fiscal_period="FY",
        period_end=date(year, 1, 31),
        revenue=revenue,
        gross_profit=revenue * Decimal("0.5"),
        operating_income=revenue * Decimal("0.2"),
        net_income=revenue * Decimal("0.15"),
        free_cash_flow=revenue * Decimal("0.12"),
        cash_and_equivalents=Decimal("40"),
        total_assets=Decimal("300"),
        total_debt=Decimal("50"),
        shareholders_equity=Decimal("180"),
        shares_outstanding=Decimal("1000"),
        source="sec_edgar",
        accession_number=f"acc-{year}",
    )


async def test_recalculate_persists_latest_and_history(client, session_factory):
    headers = await _headers(client)
    created = await client.post(
        "/companies",
        json={"name": "Quality Corp", "ticker": "QUAL"},
        headers=headers,
    )
    company_id = created.json()["id"]
    async with session_factory() as session:
        session.add_all([_stored_year(company_id, year) for year in range(2021, 2027)])
        await session.commit()

    missing = await client.post("/companies/9999/scores/quality/recalculate", headers=headers)
    first = await client.post(f"/companies/{company_id}/scores/quality/recalculate", headers=headers)
    second = await client.post(f"/companies/{company_id}/scores/quality/recalculate", headers=headers)
    latest = await client.get(f"/companies/{company_id}/scores/latest", headers=headers)
    history = await client.get(f"/companies/{company_id}/scores/history", headers=headers)

    assert missing.status_code == 404
    assert first.status_code == 200
    body = first.json()
    assert body["method_version"] == METHOD_VERSION
    assert body["ticker"] == "QUAL"
    assert Decimal(body["quality_score"]) > 0
    assert body["components"]["dilution"] is not None
    assert body["metrics"]["shares_comparable"] is True
    assert body["confidence"]["revenue_confidence"] == "HIGH"
    assert second.json()["id"] == body["id"]
    assert latest.status_code == 200
    assert latest.json()["quality_score"] == body["quality_score"]
    assert history.status_code == 200
    assert len(history.json()) == 1

    async with session_factory() as session:
        stored = (
            await session.execute(select(CompanyScore).where(CompanyScore.company_id == company_id))
        ).scalars().all()
    assert len(stored) == 1
    assert stored[0].method_version == "quality_v1"


async def test_score_routes_require_jwt(client):
    recalculated = await client.post("/companies/1/scores/quality/recalculate")
    latest = await client.get("/companies/1/scores/latest")
    history = await client.get("/companies/1/scores/history")
    assert recalculated.status_code == 401
    assert latest.status_code == 401
    assert history.status_code == 401


def test_capex_fallback_lowers_fcf_confidence_only():
    periods = _steady_history()
    metrics = build_annual_metrics(periods)
    confidence = build_confidence(periods, metrics, capex_fallback=True)
    assert confidence.fcf_confidence == "MEDIUM"
    assert confidence.revenue_confidence == "HIGH"
