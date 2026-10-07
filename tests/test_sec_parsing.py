from datetime import date
from decimal import Decimal

import httpx
import pytest

from app.services.sec.cik import cik_for_companyfacts, normalize_cik
from app.services.sec.client import SecClient
from app.services.sec.company_facts import eps_value_plausible, parse_company_facts
from app.services.sec.errors import SecCompanyFactsNotFound
from app.services.sec.free_cash_flow import compute_free_cash_flow
from app.services.sec.sync import sync_sec_financials
from app.models.company import Company
from app.models.financial_metric import FinancialMetric
from sqlalchemy import select
from tests.sec_fixtures import duration, facts_payload, instant


def test_free_cash_flow_uses_absolute_capex():
    assert compute_free_cash_flow(Decimal("100"), Decimal("30")) == Decimal("70")
    assert compute_free_cash_flow(Decimal("100"), Decimal("-30")) == Decimal("70")
    assert compute_free_cash_flow(Decimal("100"), None) is None
    assert compute_free_cash_flow(None, Decimal("30")) is None


def test_normalize_cik_strips_leading_zeros_and_pads_only_for_urls():
    assert normalize_cik("1045810") == "1045810"
    assert normalize_cik("0001045810") == "1045810"
    assert normalize_cik(1045810) == "1045810"
    assert normalize_cik(" 1045810 ") == "1045810"
    assert cik_for_companyfacts("1045810") == "0001045810"
    with pytest.raises(ValueError):
        normalize_cik("NVDA")


def test_extracts_revenue_net_income_cash_flow_and_capex():
    payload = facts_payload(
        (
            "us-gaap",
            "Revenues",
            "USD",
            [duration("2023-01-30", "2024-01-28", 60922000000, fy=2024, fp="FY")],
        ),
        (
            "us-gaap",
            "NetIncomeLoss",
            "USD",
            [duration("2023-01-30", "2024-01-28", 29760000000, fy=2024, fp="FY")],
        ),
        (
            "us-gaap",
            "NetCashProvidedByUsedInOperatingActivities",
            "USD",
            [duration("2023-01-30", "2024-01-28", 28090000000, fy=2024, fp="FY")],
        ),
        (
            "us-gaap",
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "USD",
            [duration("2023-01-30", "2024-01-28", -1069000000, fy=2024, fp="FY")],
        ),
    )

    period = parse_company_facts(payload, cik="0001045810").periods[0]

    assert period.fiscal_year == 2024
    assert period.fiscal_period == "FY"
    assert period.revenue == Decimal("60922000000")
    assert period.net_income == Decimal("29760000000")
    assert period.operating_cash_flow == Decimal("28090000000")
    assert period.capital_expenditure == Decimal("-1069000000")
    assert period.free_cash_flow == Decimal("27021000000")
    assert period.revenue_source_concept == "Revenues"
    assert period.operating_cash_flow_source_concept == "NetCashProvidedByUsedInOperatingActivities"
    assert period.capital_expenditure_source_concept == "PaymentsToAcquirePropertyPlantAndEquipment"
    assert period.period_start == date(2023, 1, 30)
    assert period.period_end == date(2024, 1, 28)
    assert period.accession_number == "0001045810-24-000001"
    assert period.source == "sec_edgar"
    assert "1045810" in period.source_url
    assert "000104581024000001" in period.source_url


def test_revenue_falls_back_to_the_next_xbrl_concept():
    payload = facts_payload(
        (
            "us-gaap",
            "SalesRevenueNet",
            "USD",
            [duration("2023-01-30", "2024-01-28", 50, fy=2024, fp="FY")],
        ),
    )

    result = parse_company_facts(payload, cik="1045810")

    assert result.periods[0].revenue == Decimal("50")
    assert result.periods[0].revenue_source_concept == "SalesRevenueNet"
    assert any("SalesRevenueNet" in warning for warning in result.warnings)


def test_revenue_prefers_the_first_concept_when_several_disagree():
    payload = facts_payload(
        (
            "us-gaap",
            "Revenues",
            "USD",
            [duration("2023-01-30", "2024-01-28", 80, fy=2024, fp="FY")],
        ),
        (
            "us-gaap",
            "SalesRevenueNet",
            "USD",
            [duration("2023-01-30", "2024-01-28", 70, fy=2024, fp="FY")],
        ),
    )

    result = parse_company_facts(payload, cik="1045810")

    assert result.periods[0].revenue == Decimal("80")
    assert any("ignored SalesRevenueNet=70" in warning for warning in result.warnings)


def test_comparative_columns_keep_the_fiscal_year_of_the_current_period():
    payload = facts_payload(
        (
            "us-gaap",
            "Revenues",
            "USD",
            [
                duration(
                    "2022-01-31",
                    "2023-01-29",
                    40,
                    fy=2024,
                    fp="FY",
                    accn="0001045810-24-000001",
                    filed="2024-02-21",
                ),
                duration(
                    "2023-01-30",
                    "2024-01-28",
                    60,
                    fy=2024,
                    fp="FY",
                    accn="0001045810-24-000001",
                    filed="2024-02-21",
                ),
                duration(
                    "2022-01-31",
                    "2023-01-29",
                    40,
                    fy=2023,
                    fp="FY",
                    accn="0001045810-23-000001",
                    filed="2023-02-24",
                ),
            ],
        ),
    )

    periods = {
        (period.fiscal_year, period.fiscal_period): period.revenue
        for period in parse_company_facts(payload, cik="1045810").periods
    }

    assert periods == {(2023, "FY"): Decimal("40"), (2024, "FY"): Decimal("60")}


def test_later_filing_restatement_replaces_the_value():
    payload = facts_payload(
        (
            "us-gaap",
            "Revenues",
            "USD",
            [
                duration(
                    "2023-01-30",
                    "2024-01-28",
                    60,
                    fy=2024,
                    fp="FY",
                    accn="0001045810-24-000001",
                    filed="2024-02-21",
                ),
                duration(
                    "2023-01-30",
                    "2024-01-28",
                    61,
                    fy=2025,
                    fp="FY",
                    accn="0001045810-25-000001",
                    filed="2025-02-26",
                ),
            ],
        ),
    )

    result = parse_company_facts(payload, cik="1045810")
    period = result.periods[0]

    assert period.fiscal_year == 2024
    assert period.revenue == Decimal("61")
    assert period.accession_number == "0001045810-25-000001"
    assert any("other filings reported" in warning for warning in result.warnings)


def test_filing_fy_tag_does_not_merge_two_quarters():
    payload = facts_payload(
        (
            "us-gaap",
            "Revenues",
            "USD",
            [
                duration("2019-01-28", "2020-01-26", 10, fy=2020, fp="FY", accn="0001045810-20-000010"),
                duration("2020-01-27", "2021-01-31", 11, fy=2021, fp="FY", accn="0001045810-21-000010"),
                duration(
                    "2019-01-28",
                    "2019-04-28",
                    3,
                    fy=2020,
                    fp="Q1",
                    form="10-Q",
                    accn="0001045810-19-000040",
                    filed="2019-05-16",
                ),
                duration(
                    "2019-01-28",
                    "2019-04-28",
                    3,
                    fy=2020,
                    fp="Q1",
                    form="10-Q",
                    accn="0001045810-20-000065",
                    filed="2020-05-21",
                ),
                duration(
                    "2020-01-27",
                    "2020-04-26",
                    4,
                    fy=2020,
                    fp="Q1",
                    form="10-Q",
                    accn="0001045810-20-000065",
                    filed="2020-05-21",
                ),
            ],
        ),
    )

    periods = {
        (period.fiscal_year, period.fiscal_period): period.revenue
        for period in parse_company_facts(payload, cik="1045810").periods
    }

    assert periods[(2020, "Q1")] == Decimal("3")
    assert periods[(2021, "Q1")] == Decimal("4")


def test_year_to_date_quarter_is_not_used_as_the_quarter():
    payload = facts_payload(
        (
            "us-gaap",
            "Revenues",
            "USD",
            [
                duration(
                    "2024-04-29",
                    "2024-07-28",
                    30,
                    fy=2025,
                    fp="Q2",
                    form="10-Q",
                    accn="0001045810-24-000010",
                    filed="2024-08-28",
                ),
                duration(
                    "2024-01-29",
                    "2024-07-28",
                    99,
                    fy=2025,
                    fp="Q2",
                    form="10-Q",
                    accn="0001045810-24-000010",
                    filed="2024-08-28",
                ),
            ],
        ),
    )

    periods = parse_company_facts(payload, cik="1045810").periods

    assert len(periods) == 1
    assert periods[0].fiscal_period == "Q2"
    assert periods[0].revenue == Decimal("30")


def test_total_debt_does_not_add_the_current_portion_twice():
    shared = {"fy": 2024, "fp": "FY"}
    payload = facts_payload(
        (
            "us-gaap",
            "Revenues",
            "USD",
            [duration("2023-01-30", "2024-01-28", 10, **shared)],
        ),
        (
            "us-gaap",
            "LongTermDebt",
            "USD",
            [instant("2024-01-28", 100, **shared)],
        ),
        (
            "us-gaap",
            "LongTermDebtNoncurrent",
            "USD",
            [instant("2024-01-28", 80, **shared)],
        ),
        (
            "us-gaap",
            "LongTermDebtCurrent",
            "USD",
            [instant("2024-01-28", 20, **shared)],
        ),
    )

    result = parse_company_facts(payload, cik="1045810")

    assert result.periods[0].total_debt == Decimal("100")
    assert result.periods[0].debt_source_concept == "LongTermDebt"
    assert any("was not added again" in warning for warning in result.warnings)


def test_capex_falls_back_to_productive_assets():
    payload = facts_payload(
        (
            "us-gaap",
            "NetCashProvidedByUsedInOperatingActivities",
            "USD",
            [duration("2023-01-30", "2024-01-28", 100, fy=2024, fp="FY")],
        ),
        (
            "us-gaap",
            "PaymentsToAcquireProductiveAssets",
            "USD",
            [duration("2023-01-30", "2024-01-28", 40, fy=2024, fp="FY")],
        ),
    )

    result = parse_company_facts(payload, cik="1045810")

    assert result.periods[0].capital_expenditure == Decimal("40")
    assert result.periods[0].capital_expenditure_source_concept == "PaymentsToAcquireProductiveAssets"
    assert result.periods[0].free_cash_flow == Decimal("60")
    assert any("PaymentsToAcquireProductiveAssets" in warning for warning in result.warnings)


def test_eps_rejects_share_count_magnitude_mis_tagged_as_eps():
    """SWK-style filings put weighted-average shares under EarningsPerShare*."""
    assert eps_value_plausible(Decimal("2.81"))
    assert not eps_value_plausible(Decimal("112000000"))
    assert not eps_value_plausible(
        Decimal("161781000"),
        shares=Decimal("154127089"),
    )

    payload = facts_payload(
        (
            "us-gaap",
            "Revenues",
            "USD",
            [duration("2020-12-29", "2021-04-03", 5000000000, fy=2021, fp="Q1", form="10-Q", filed="2021-05-01")],
        ),
        (
            "us-gaap",
            "NetIncomeLoss",
            "USD",
            [duration("2020-12-29", "2021-04-03", 133200000, fy=2021, fp="Q1", form="10-Q", filed="2021-05-01")],
        ),
        (
            "us-gaap",
            "EarningsPerShareBasic",
            "USD/shares",
            [duration("2020-12-29", "2021-04-03", "2.81", fy=2021, fp="Q1", form="10-Q", filed="2021-05-01")],
        ),
        (
            "us-gaap",
            "EarningsPerShareDiluted",
            "USD/shares",
            [
                duration(
                    "2020-12-29",
                    "2021-04-03",
                    112000000,
                    fy=2021,
                    fp="Q1",
                    form="10-Q",
                    filed="2021-05-10",
                    accn="0000093556-21-000099",
                ),
                duration(
                    "2020-12-29",
                    "2021-04-03",
                    "2.75",
                    fy=2021,
                    fp="Q1",
                    form="10-Q",
                    filed="2021-05-01",
                    accn="0000093556-21-000050",
                ),
            ],
        ),
        (
            "us-gaap",
            "CommonStockSharesOutstanding",
            "shares",
            [instant("2021-04-03", 154127089, fy=2021, fp="Q1", form="10-Q", filed="2021-05-01")],
        ),
    )

    result = parse_company_facts(payload, cik="93556")
    period = result.periods[0]
    assert period.revenue == Decimal("5000000000")
    assert period.eps_basic == Decimal("2.81")
    assert period.eps_diluted == Decimal("2.75")
    assert period.eps_diluted_source_concept == "EarningsPerShareDiluted"
    assert any("non-EPS magnitude" in warning or "implausible" in warning for warning in result.warnings)


async def test_sec_sync_keeps_period_when_only_eps_diluted_is_invalid(session_factory):
    payload = facts_payload(
        (
            "us-gaap",
            "Revenues",
            "USD",
            [duration("2020-12-29", "2021-04-03", 5000000000, fy=2021, fp="Q1", form="10-Q")],
        ),
        (
            "us-gaap",
            "EarningsPerShareBasic",
            "USD/shares",
            [duration("2020-12-29", "2021-04-03", "2.81", fy=2021, fp="Q1", form="10-Q")],
        ),
        (
            "us-gaap",
            "EarningsPerShareDiluted",
            "USD/shares",
            [duration("2020-12-29", "2021-04-03", 112000000, fy=2021, fp="Q1", form="10-Q")],
        ),
    )

    class _Client:
        async def get_company_facts(self, cik: str) -> dict:
            return payload

    async with session_factory() as session:
        company = Company(name="Stanley Sync", ticker="SWKT", sec_cik="0000093556")
        session.add(company)
        await session.commit()
        await session.refresh(company)
        result = await sync_sec_financials(session, company, _Client())
        metric = (
            await session.execute(
                select(FinancialMetric).where(FinancialMetric.company_id == company.id)
            )
        ).scalar_one()

    assert result.created == 1
    assert metric.revenue == Decimal("5000000000")
    assert metric.eps_basic == Decimal("2.81")
    assert metric.eps_diluted is None
    assert any("eps_diluted" in warning for warning in result.warnings)


async def test_sec_client_pads_cik_and_sends_user_agent():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["user_agent"] = request.headers["user-agent"]
        return httpx.Response(200, json={"facts": {}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = SecClient(
            "Sentinel contact@example.com",
            http_client=http_client,
            min_interval_seconds=0,
        )
        payload = await client.get_company_facts("1045810")

    assert payload == {"facts": {}}
    assert seen["url"].endswith("/CIK0001045810.json")
    assert seen["user_agent"] == "Sentinel contact@example.com"


async def test_sec_client_maps_404_without_retrying_forever():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = SecClient(
            "Sentinel contact@example.com",
            http_client=http_client,
            min_interval_seconds=0,
            max_retries=3,
        )
        with pytest.raises(SecCompanyFactsNotFound):
            await client.get_company_facts("1045810")

    assert calls["count"] == 1
