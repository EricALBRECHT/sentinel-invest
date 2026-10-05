import json

from app.core.config import settings
from tests.test_quality_score import _headers

_SENSITIVE_KEYS = ("password", "secret", "jwt", "dsn", "token", "credential")


def _keys(value, found: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            found.append(str(key).lower())
            _keys(item, found)
    elif isinstance(value, list):
        for item in value:
            _keys(item, found)


async def test_admin_status_requires_jwt(client):
    response = await client.get("/admin/status")
    assert response.status_code == 401


async def test_admin_status_reports_live_checks_and_counts(client):
    headers = await _headers(client, email="admin-status@example.com")
    created = await client.post(
        "/companies",
        json={"name": "Status Corp", "ticker": "STAT", "sec_cik": "1045810"},
        headers=headers,
    )
    company_id = created.json()["id"]
    await client.put(
        f"/companies/{company_id}/opportunity-profile",
        json={
            "megatrends_json": [
                {"trend": "AI", "exposure_score": 100, "confidence": 80, "source": "MANUAL_STRUCTURED"}
            ]
        },
        headers=headers,
    )
    await client.post(f"/companies/{company_id}/scores/opportunity/recalculate", headers=headers)

    response = await client.get("/admin/status", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["system"]["api"] == "ok"
    assert body["system"]["postgres"] == "ok"
    assert body["system"]["redis"] == "ok"
    assert body["data"]["users"] == 1
    assert body["data"]["companies"] == 1
    assert body["data"]["financial_metrics"] == 0
    assert body["data"]["quality_scores"] == 0
    assert body["data"]["opportunity_scores"] == 1
    assert body["analysis"]["opportunity_incomplete"] == 1
    assert body["analysis"]["opportunity_partial"] == 0
    assert body["analysis"]["opportunity_usable"] == 0
    assert body["analysis"]["opportunity_complete"] == 0
    assert body["analysis"]["ranking_eligible"] == 0
    assert body["sec"]["companies_with_cik"] == 1
    assert body["sec"]["companies_without_cik"] == 0
    assert body["server"]["started_at"]
    assert body["server"]["uptime_seconds"] >= 0

    keys: list[str] = []
    _keys(body, keys)
    assert all(token not in key for key in keys for token in _SENSITIVE_KEYS)
    raw = json.dumps(body)
    assert "postgres://" not in raw
    assert "redis://" not in raw
    assert settings.postgres_password not in raw
    assert settings.jwt_secret_key not in raw
