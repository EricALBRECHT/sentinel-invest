from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.api.routes.financials import get_sec_client
from app.main import app
from app.models.company import Company
from app.models.financial_metric import FinancialMetric
from tests.sec_fixtures import duration, facts_payload


async def _headers(client, email="analyst@example.com"):
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


def _metric(company_id: int, year: int, *, accession: str, revenue: str = "10") -> FinancialMetric:
    return FinancialMetric(
        company_id=company_id,
        fiscal_year=year,
        fiscal_period="FY",
        period_start=date(year - 1, 2, 1),
        period_end=date(year, 1, 31),
        filed_at=date(year, 2, 20),
        revenue=Decimal(revenue),
        source="sec_edgar",
        accession_number=accession,
    )


async def test_create_financial_metric(session_factory):
    async with session_factory() as session:
        company = Company(name="NVIDIA Corporation", ticker="NVDA")
        session.add(company)
        await session.commit()
        await session.refresh(company)

        session.add(
            FinancialMetric(
                company_id=company.id,
                fiscal_year=2024,
                fiscal_period="FY",
                period_end=date(2024, 1, 28),
                revenue=Decimal("60922000000.00"),
                eps_diluted=Decimal("1.1900"),
                source="sec_edgar",
                accession_number="0001045810-24-000001",
            )
        )
        await session.commit()

        stored = await session.get(FinancialMetric, 1)

    assert stored is not None
    assert stored.company_id == company.id
    assert stored.fiscal_year == 2024
    assert stored.fiscal_period == "FY"
    assert stored.revenue == Decimal("60922000000.00")
    assert stored.eps_diluted == Decimal("1.1900")
    assert stored.created_at is not None
    assert stored.updated_at is not None


async def test_duplicate_fiscal_period_is_rejected(session_factory):
    async with session_factory() as session:
        company = Company(name="NVIDIA Corporation", ticker="NVDA")
        session.add(company)
        await session.commit()
        await session.refresh(company)
        session.add(_metric(company.id, 2024, accession="0001045810-24-000001"))
        await session.commit()
        session.add(_metric(company.id, 2024, accession="0001045810-25-000001"))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


async def test_invalid_fiscal_period_is_rejected(session_factory):
    async with session_factory() as session:
        company = Company(name="NVIDIA Corporation", ticker="NVDA")
        session.add(company)
        await session.commit()
        await session.refresh(company)
        session.add(
            FinancialMetric(
                company_id=company.id,
                fiscal_year=2024,
                fiscal_period="H1",
                source="sec_edgar",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


async def test_patch_normalizes_sec_cik(client):
    headers = await _headers(client)
    created = await client.post(
        "/companies",
        json={"name": "NVIDIA Corporation", "ticker": "nvda"},
        headers=headers,
    )
    company_id = created.json()["id"]

    patched = await client.patch(
        f"/companies/{company_id}",
        json={"sec_cik": "0001045810"},
        headers=headers,
    )
    invalid = await client.patch(
        f"/companies/{company_id}",
        json={"sec_cik": "NVDA"},
        headers=headers,
    )

    assert patched.status_code == 200
    assert patched.json()["sec_cik"] == "1045810"
    assert invalid.status_code == 422


async def test_sec_sync_missing_company_returns_404(client):
    headers = await _headers(client)
    response = await client.post("/companies/9999/financials/sec-sync", headers=headers)
    assert response.status_code == 404


async def test_sec_sync_without_cik_returns_400(client):
    headers = await _headers(client)
    created = await client.post(
        "/companies",
        json={"name": "NVIDIA Corporation", "ticker": "nvda"},
        headers=headers,
    )
    response = await client.post(
        f"/companies/{created.json()['id']}/financials/sec-sync",
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Company has no SEC CIK"


async def test_sec_sync_persists_mocked_facts_and_is_idempotent(client):
    headers = await _headers(client, email="sync@example.com")
    created = await client.post(
        "/companies",
        json={"name": "NVIDIA Corporation", "ticker": "nvda", "sec_cik": "1045810"},
        headers=headers,
    )
    company_id = created.json()["id"]
    payload = facts_payload(
        (
            "us-gaap",
            "Revenues",
            "USD",
            [duration("2023-01-30", "2024-01-28", 60, fy=2024, fp="FY")],
        ),
        (
            "us-gaap",
            "NetCashProvidedByUsedInOperatingActivities",
            "USD",
            [duration("2023-01-30", "2024-01-28", 100, fy=2024, fp="FY")],
        ),
        (
            "us-gaap",
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "USD",
            [duration("2023-01-30", "2024-01-28", 25, fy=2024, fp="FY")],
        ),
    )

    class FakeSec:
        async def get_company_facts(self, cik: str) -> dict:
            assert cik == "1045810"
            return payload

        async def aclose(self) -> None:
            return None

    app.dependency_overrides[get_sec_client] = lambda: FakeSec()
    first = await client.post(f"/companies/{company_id}/financials/sec-sync", headers=headers)
    second = await client.post(f"/companies/{company_id}/financials/sec-sync", headers=headers)
    listed = await client.get(f"/companies/{company_id}/financials", headers=headers)

    assert first.status_code == 200
    assert first.json()["created"] == 1
    assert first.json()["periods_found"] == 1
    assert second.json()["created"] == 0
    assert second.json()["skipped"] == 1
    body = listed.json()
    assert Decimal(body[0]["revenue"]) == Decimal("60")
    assert Decimal(body[0]["free_cash_flow"]) == Decimal("75")
    assert body[0]["source"] == "sec_edgar"


async def test_financials_require_jwt(client):
    response = await client.get("/companies/1/financials")
    latest = await client.get("/companies/1/financials/latest")
    sync = await client.post("/companies/1/financials/sec-sync")
    assert response.status_code == 401
    assert latest.status_code == 401
    assert sync.status_code == 401


async def test_list_financials_filters_pagination_and_latest(client, session_factory):
    headers = await _headers(client, email="reader@example.com")
    created = await client.post(
        "/companies",
        json={"name": "NVIDIA Corporation", "ticker": "nvda"},
        headers=headers,
    )
    company_id = created.json()["id"]
    async with session_factory() as session:
        session.add_all(
            [
                _metric(company_id, 2022, accession="a", revenue="20"),
                _metric(company_id, 2023, accession="b", revenue="30"),
                _metric(company_id, 2024, accession="c", revenue="40"),
                FinancialMetric(
                    company_id=company_id,
                    fiscal_year=2025,
                    fiscal_period="Q1",
                    period_start=date(2024, 1, 29),
                    period_end=date(2024, 4, 28),
                    revenue=Decimal("15"),
                    source="sec_edgar",
                    accession_number="d",
                ),
            ]
        )
        await session.commit()

    listed = await client.get(f"/companies/{company_id}/financials", headers=headers)
    window = await client.get(
        f"/companies/{company_id}/financials",
        params={"year_from": 2023, "year_to": 2024, "fiscal_period": "FY"},
        headers=headers,
    )
    page = await client.get(
        f"/companies/{company_id}/financials",
        params={"limit": 1, "offset": 1},
        headers=headers,
    )
    latest = await client.get(f"/companies/{company_id}/financials/latest", headers=headers)

    assert listed.status_code == 200
    assert [item["fiscal_year"] for item in listed.json()] == [2025, 2024, 2023, 2022]
    assert [item["fiscal_year"] for item in window.json()] == [2024, 2023]
    assert [item["fiscal_year"] for item in page.json()] == [2024]
    assert latest.status_code == 200
    assert latest.json()["fiscal_year"] == 2025
    assert latest.json()["fiscal_period"] == "Q1"
