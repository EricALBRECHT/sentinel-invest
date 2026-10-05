from datetime import datetime
from decimal import Decimal


def _as_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


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


def _nvidia():
    return {
        "name": "  NVIDIA Corporation  ",
        "ticker": "nvda",
        "isin": "us67066g1040",
        "country": "US",
        "exchange": "NASDAQ",
        "sector": "Technology",
        "industry": "Semiconductors",
        "market_cap": "3500000000000.00",
        "pea_eligible": False,
    }


async def test_create_company_normalizes_fields(client):
    headers = await _headers(client)
    response = await client.post("/companies", json=_nvidia(), headers=headers)

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "NVIDIA Corporation"
    assert body["ticker"] == "NVDA"
    assert body["isin"] == "US67066G1040"
    assert body["country"] == "US"
    assert body["exchange"] == "NASDAQ"
    assert body["sector"] == "Technology"
    assert body["industry"] == "Semiconductors"
    assert Decimal(body["market_cap"]) == Decimal("3500000000000.00")
    assert body["pea_eligible"] is False
    assert body["created_at"]
    assert body["updated_at"]


async def test_duplicate_name_ticker_and_isin(client):
    headers = await _headers(client)
    assert (await client.post("/companies", json=_nvidia(), headers=headers)).status_code == 201

    duplicate_name = await client.post(
        "/companies",
        json={"name": "NVIDIA Corporation", "ticker": "NVDAX"},
        headers=headers,
    )
    duplicate_ticker = await client.post(
        "/companies",
        json={"name": "Nvidia Inc", "ticker": "nvda"},
        headers=headers,
    )
    duplicate_isin = await client.post(
        "/companies",
        json={"name": "Other Chip", "ticker": "OTHR", "isin": "US67066G1040"},
        headers=headers,
    )

    assert duplicate_name.status_code == 409
    assert "name" in duplicate_name.json()["detail"]
    assert duplicate_ticker.status_code == 409
    assert "ticker" in duplicate_ticker.json()["detail"]
    assert duplicate_isin.status_code == 409
    assert "ISIN" in duplicate_isin.json()["detail"]


async def test_get_company_by_id(client):
    headers = await _headers(client)
    created = await client.post("/companies", json=_nvidia(), headers=headers)
    company_id = created.json()["id"]

    fetched = await client.get(f"/companies/{company_id}", headers=headers)
    missing = await client.get("/companies/9999", headers=headers)

    assert fetched.status_code == 200
    assert fetched.json()["ticker"] == "NVDA"
    assert missing.status_code == 404


async def test_patch_partial_update_and_missing_company(client):
    headers = await _headers(client)
    created = await client.post("/companies", json=_nvidia(), headers=headers)
    original = created.json()

    patched = await client.patch(
        f"/companies/{original['id']}",
        json={"sector": "Semiconductors", "pea_eligible": True},
        headers=headers,
    )
    missing = await client.patch("/companies/9999", json={"sector": "Energy"}, headers=headers)

    assert patched.status_code == 200
    body = patched.json()
    assert body["sector"] == "Semiconductors"
    assert body["pea_eligible"] is True
    assert body["name"] == "NVIDIA Corporation"
    assert body["ticker"] == "NVDA"
    assert _as_datetime(body["updated_at"]) >= _as_datetime(original["updated_at"])
    assert missing.status_code == 404


async def test_patch_duplicate_name_ticker_and_isin(client):
    headers = await _headers(client)
    first = await client.post("/companies", json=_nvidia(), headers=headers)
    second = await client.post(
        "/companies",
        json={"name": "STMicroelectronics", "ticker": "stm", "isin": "nl0000226223"},
        headers=headers,
    )
    company_id = second.json()["id"]

    name_conflict = await client.patch(
        f"/companies/{company_id}",
        json={"name": "NVIDIA Corporation"},
        headers=headers,
    )
    ticker_conflict = await client.patch(
        f"/companies/{company_id}",
        json={"ticker": "nvda"},
        headers=headers,
    )
    isin_conflict = await client.patch(
        f"/companies/{company_id}",
        json={"isin": "us67066g1040"},
        headers=headers,
    )

    assert first.status_code == 201
    assert name_conflict.status_code == 409
    assert ticker_conflict.status_code == 409
    assert isin_conflict.status_code == 409
    unchanged = await client.get(f"/companies/{company_id}", headers=headers)
    assert unchanged.json()["ticker"] == "STM"
    assert unchanged.json()["isin"] == "NL0000226223"


async def test_search_by_name_and_ticker(client):
    headers = await _headers(client)
    await client.post("/companies", json=_nvidia(), headers=headers)
    await client.post(
        "/companies",
        json={"name": "STMicroelectronics", "ticker": "stm"},
        headers=headers,
    )

    by_name = await client.get("/companies", params={"q": "nvidia"}, headers=headers)
    by_ticker = await client.get("/companies", params={"q": "NVDA"}, headers=headers)
    by_stm = await client.get("/companies", params={"q": "stm"}, headers=headers)

    assert [item["ticker"] for item in by_name.json()] == ["NVDA"]
    assert [item["ticker"] for item in by_ticker.json()] == ["NVDA"]
    assert [item["ticker"] for item in by_stm.json()] == ["STM"]


async def test_pagination(client):
    headers = await _headers(client)
    for name, ticker in (("Alpha", "ALP"), ("Beta", "BET"), ("Gamma", "GAM")):
        created = await client.post(
            "/companies",
            json={"name": name, "ticker": ticker},
            headers=headers,
        )
        assert created.status_code == 201

    first_page = await client.get("/companies", params={"limit": 2, "offset": 0}, headers=headers)
    second_page = await client.get("/companies", params={"limit": 2, "offset": 2}, headers=headers)

    assert [item["ticker"] for item in first_page.json()] == ["ALP", "BET"]
    assert [item["ticker"] for item in second_page.json()] == ["GAM"]


async def test_company_routes_require_jwt(client):
    listed = await client.get("/companies")
    created = await client.post("/companies", json={"name": "Acme", "ticker": "ACME"})
    fetched = await client.get("/companies/1")
    patched = await client.patch("/companies/1", json={"sector": "Energy"})

    assert listed.status_code == 401
    assert created.status_code == 401
    assert fetched.status_code == 401
    assert patched.status_code == 401


async def test_market_cap_must_be_non_negative(client):
    headers = await _headers(client)
    response = await client.post(
        "/companies",
        json={"name": "Acme", "ticker": "ACME", "market_cap": -1},
        headers=headers,
    )
    assert response.status_code == 422
