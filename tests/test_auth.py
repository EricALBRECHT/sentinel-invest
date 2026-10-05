from datetime import timedelta

import jwt
from sqlalchemy import select

from app.core.config import settings
from app.core.security import create_access_token
from app.models.user import User


async def _register(client, email="user@example.com", password="password123"):
    return await client.post("/auth/register", json={"email": email, "password": password})


async def _login(client, email="user@example.com", password="password123"):
    return await client.post(
        "/auth/login",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


async def test_health_is_public(client):
    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert "status" in body
    assert "postgres" in body
    assert "redis" in body


async def test_register_and_login(client):
    created = await _register(client)
    assert created.status_code == 201
    body = created.json()
    assert body["email"] == "user@example.com"
    assert body["is_active"] is True
    assert "hashed_password" not in body
    assert "password" not in body

    token_response = await _login(client)
    assert token_response.status_code == 200
    token = token_response.json()
    assert token["token_type"] == "bearer"
    assert token["access_token"]


async def test_register_rejects_short_password(client):
    response = await _register(client, password="short")
    assert response.status_code == 422


async def test_register_rejects_duplicate_email(client):
    assert (await _register(client)).status_code == 201
    duplicate = await _register(client, email="User@example.com")
    assert duplicate.status_code == 409


async def test_login_oauth2_form_urlencoded(client):
    email = "swagger@example.com"
    password = "password123"
    assert (await _register(client, email=email, password=password)).status_code == 201

    response = await client.post(
        "/auth/login",
        data={
            "grant_type": "password",
            "username": email,
            "password": password,
            "client_id": "",
            "client_secret": "",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]

    companies = await client.get(
        "/companies",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert companies.status_code == 200


async def test_login_rejects_unknown_user_and_wrong_password(client):
    assert (await _register(client)).status_code == 201

    unknown = await _login(client, email="missing@example.com")
    wrong = await _login(client, password="wrong-password")

    assert unknown.status_code == 401
    assert wrong.status_code == 401
    assert unknown.json()["detail"] == wrong.json()["detail"]


async def test_inactive_user_cannot_login(client, session_factory):
    assert (await _register(client)).status_code == 201

    async with session_factory() as session:
        user = (await session.execute(select(User).where(User.email == "user@example.com"))).scalar_one()
        user.is_active = False
        await session.commit()

    response = await _login(client)
    assert response.status_code == 401


async def test_companies_require_a_valid_token(client):
    open_response = await client.get("/companies")
    assert open_response.status_code == 401

    created = await _register(client)
    assert created.status_code == 201
    token = (await _login(client)).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    listed = await client.get("/companies", headers=headers)
    assert listed.status_code == 200
    assert listed.json() == []

    created_company = await client.post(
        "/companies",
        json={"name": "Acme", "ticker": "acme"},
        headers=headers,
    )
    assert created_company.status_code == 201
    company_id = created_company.json()["id"]

    fetched = await client.get(f"/companies/{company_id}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "Acme"

    missing = await client.get("/companies", headers={"Authorization": "Bearer not-a-token"})
    assert missing.status_code == 401

    foreign = jwt.encode({"sub": "1"}, "another-secret-that-is-long-enough", algorithm="HS256")
    forged = await client.get("/companies", headers={"Authorization": f"Bearer {foreign}"})
    assert forged.status_code == 401


async def test_expired_token_is_rejected(client):
    assert (await _register(client)).status_code == 201
    login = await _login(client)
    user_id = jwt.decode(
        login.json()["access_token"],
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
    )["sub"]
    expired = create_access_token(user_id, expires_delta=timedelta(minutes=-5))

    response = await client.get("/companies", headers={"Authorization": f"Bearer {expired}"})
    assert response.status_code == 401


async def test_deactivated_user_token_is_rejected(client, session_factory):
    assert (await _register(client)).status_code == 201
    token = (await _login(client)).json()["access_token"]

    async with session_factory() as session:
        user = (await session.execute(select(User))).scalar_one()
        user.is_active = False
        await session.commit()

    response = await client.get("/companies", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
