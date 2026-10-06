"""The HTML pages stay behind the cookie. The JSON API stays on Bearer tokens."""

import re
from datetime import date, timedelta
from decimal import Decimal

from app.core.config import settings
from app.models.company import Company
from app.models.company_market_snapshot import CompanyMarketSnapshot
from app.models.company_score import CompanyScore
from app.models.investment_view import InvestmentView
from app.models.market_price import MarketPrice
from app.models.opportunity_score import OpportunityScore
from app.models.supply_chain import DiscoveredCompany
from app.models.technical_snapshot import TechnicalSnapshot
from app.web.i18n import translate_warning

_SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.IGNORECASE | re.DOTALL)
_SRC = re.compile(r"""src=["']([^"']+)["']""", re.IGNORECASE)
_TYPE = re.compile(r"""type=["']([^"']+)["']""", re.IGNORECASE)
_CDN = ("unpkg.com", "jsdelivr.net", "cdnjs.cloudflare.com", "googleapis.com")


def _assert_local_html(response):
    policy = response.headers["content-security-policy"]
    assert "default-src 'self'" in policy
    assert "script-src 'self'" in policy
    assert "'unsafe-eval'" not in policy
    assert "no-cache" in response.headers["cache-control"]
    for host in _CDN:
        assert host not in response.text
    for attrs, body in _SCRIPT.findall(response.text):
        source = _SRC.search(attrs)
        if source:
            assert source.group(1).startswith("/static/")
            assert "://" not in source.group(1)
            continue
        kind = _TYPE.search(attrs)
        assert kind is not None and kind.group(1) == "application/json"
        assert not body.strip().startswith(("function", "var ", "const ", "let ", "("))


async def _login(client, email="web@example.com", password="password123"):
    await client.post("/auth/register", json={"email": email, "password": password})
    return await client.post(
        "/login",
        data={"email": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


async def test_web_login_logout_and_dashboard_guard(client):
    assert (await client.get("/dashboard")).status_code == 303
    assert (await client.get("/companies/2/view")).status_code == 303
    assert (await client.get("/discovery")).status_code == 303
    assert (await client.get("/admin/view")).status_code == 303
    login_page = await client.get("/login")
    assert login_page.status_code == 200
    assert "Se connecter" in login_page.text
    assert "/static/vendor/htmx.min.js" in login_page.text
    _assert_local_html(login_page)
    assert settings.jwt_secret_key not in login_page.text
    assert "Mot de passe" in login_page.text
    denied = await client.post("/login", data={"email": "missing@example.com", "password": "WrongUniqueSecret99"})
    assert denied.status_code == 401
    assert "WrongUniqueSecret99" not in denied.text
    assert "Identifiants incorrects" in denied.text

    logged = await _login(client)
    assert logged.status_code == 303
    assert logged.headers["location"] == "/dashboard"
    cookie = logged.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    token = logged.cookies.get("sentinel_token")
    assert token
    page = await client.get("/dashboard")
    assert page.status_code == 200
    assert "Tableau de bord" in page.text
    assert token not in page.text
    assert settings.jwt_secret_key not in page.text
    assert settings.postgres_password not in page.text
    assert settings.sec_user_agent not in page.text
    assert "postgresql://" not in page.text
    assert "redis://" not in page.text

    api = await client.post(
        "/auth/login",
        data={"username": "web@example.com", "password": "password123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert api.status_code == 200
    assert api.json()["access_token"]

    assert (await client.post("/logout")).status_code == 303
    assert (await client.get("/dashboard")).status_code == 303
    client.cookies.set("sentinel_token", "not-a-jwt")
    expired = await client.get("/login")
    assert "Session expirée" in expired.text


async def test_dashboard_company_discovery_and_admin(client, session_factory):
    async with session_factory() as session:
        nvidia = Company(
            name="NVIDIA Corporation",
            ticker="NVDA",
            exchange="NASDAQ",
            universe_status="WATCHED",
            universe_priority=25,
            discovery_source="NASDAQ100",
            market_cap=Decimal("5724714000000"),
            is_active=True,
        )
        session.add(nvidia)
        await session.flush()
        session.add(
            CompanyScore(
                company_id=nvidia.id,
                score_date=date(2026, 10, 5),
                quality_score=Decimal("98.79"),
                overall_confidence_score=72,
                metrics_json={
                    "revenue_growth_1y": "0.65",
                    "gross_margin": "0.70",
                    "operating_margin": "0.60",
                    "net_margin": "0.55",
                    "roe": "0.80",
                    "roa": "0.40",
                },
                confidence_json={},
                method_version="quality_v1",
            )
        )
        session.add(
            TechnicalSnapshot(
                company_id=nvidia.id,
                as_of_date=date(2026, 10, 5),
                price=Decimal("237.54"),
                sma_20=Decimal("210"),
                sma_50=Decimal("190"),
                sma_100=Decimal("170"),
                sma_200=Decimal("150"),
                rsi_14=Decimal("66"),
                macd=Decimal("1.2"),
                macd_signal=Decimal("1"),
                macd_histogram=Decimal("0.2"),
                atr_14=Decimal("4"),
                volatility_20d=Decimal("0.25"),
                volume_ratio=Decimal("1.1"),
                trend_short="UP",
                trend_medium="UP",
                trend_long="STRONG_UP",
                support_1=Decimal("200"),
                support_2=Decimal("180"),
                resistance_1=Decimal("250"),
                resistance_2=Decimal("270"),
                technical_score=Decimal("87.00"),
                technical_confidence=100,
                components_json={},
            )
        )
        session.add(
            OpportunityScore(
                company_id=nvidia.id,
                score_date=date(2026, 10, 5),
                opportunity_score=Decimal("81.11"),
                opportunity_confidence_score=42,
                coverage_score=45,
                coverage_status="PARTIAL",
                ranking_eligible=False,
                components_json={},
                confidence_json={},
                method_version="opportunity_v1",
            )
        )
        session.add(
            InvestmentView(
                company_id=nvidia.id,
                as_of_date=date(2026, 10, 5),
                method="investment_view_v1",
                long_term_conviction_score=Decimal("70.76"),
                long_term_conviction_label="GOOD",
                entry_attractiveness_score=Decimal("87.00"),
                entry_attractiveness_label="STRONG",
                analysis_readiness_score=Decimal("78.00"),
                analysis_readiness_label="USABLE",
                components_json={
                    "warnings": [
                        "Opportunity coverage is below ranking threshold.",
                        "Untranslated custom warning.",
                    ]
                },
            )
        )
        session.add(
            CompanyMarketSnapshot(
                company_id=nvidia.id,
                price=Decimal("237.54"),
                last_market_date=date(2026, 10, 5),
                market_cap=Decimal("5724714000000"),
                source="yahoo",
            )
        )
        start = date(2026, 8, 1)
        for offset in range(40):
            session.add(
                MarketPrice(
                    company_id=nvidia.id,
                    trade_date=start + timedelta(days=offset),
                    close=Decimal("100") + offset,
                    adjusted_close=Decimal("188.50") if offset == 39 else Decimal("100") + offset,
                    volume=1000 + offset,
                    source="yahoo",
                )
            )
        core = Company(
            name="CoreWeave, Inc.",
            ticker="CRWV",
            universe_status="DISCOVERED",
            discovery_source="SUPPLY_CHAIN",
            discovery_pipeline_status="ANALYZED",
            discovery_depth=1,
            discovered_parent_company_id=nvidia.id,
            is_active=True,
        )
        session.add(core)
        await session.flush()
        session.add(
            DiscoveredCompany(
                name="Acer",
                discovered_from_company_id=nvidia.id,
                discovery_reason="PARTNER via manufacturer_partners with NVIDIA",
                evidence_count=1,
                confidence=90,
                status="CANDIDATE",
                verification_status="PARTIAL",
                document_ids=[],
                verification_evidence_json={},
            )
        )
        session.add(
            DiscoveredCompany(
                name="OpenAI",
                discovered_from_company_id=nvidia.id,
                discovery_reason="CUSTOMER via running_on with NVIDIA",
                evidence_count=1,
                confidence=75,
                status="CANDIDATE",
                verification_status="PARTIAL",
                document_ids=[],
                verification_evidence_json={},
            )
        )
        session.add(
            DiscoveredCompany(
                name="CoreWeave",
                discovered_from_company_id=nvidia.id,
                promoted_company_id=core.id,
                discovery_reason="PARTNER via co_engineering with NVIDIA",
                evidence_count=1,
                confidence=90,
                status="IMPORTED",
                verification_status="VERIFIED",
                document_ids=[],
                verification_evidence_json={},
            )
        )
        await session.commit()
        nvidia_id = nvidia.id
        core_id = core.id

    await _login(client, email="pages@example.com")
    dashboard = await client.get("/dashboard")
    assert dashboard.status_code == 200
    assert "NVIDIA Corporation" in dashboard.text
    assert "/static/vendor/htmx.min.js" in dashboard.text
    assert "data-auto-submit" in dashboard.text
    assert "onchange=" not in dashboard.text
    _assert_local_html(dashboard)
    assert "NVDA" in dashboard.text
    assert "98,79" in dashboard.text
    assert "Surveillée" in dashboard.text
    assert "Découverte" in dashboard.text
    assert "<th>Qualité</th>" in dashboard.text
    assert "<th>Quality</th>" not in dashboard.text
    assert "Forte" in dashboard.text
    assert "Exploitable" in dashboard.text
    assert "Partielle" in dashboard.text
    assert "05/10/2026" in dashboard.text
    filtered = await client.get("/dashboard", headers={"HX-Request": "true"}, params={"universe_status": "WATCHED"})
    assert "NVDA" in filtered.text
    assert "CRWV" not in filtered.text
    assert "content-security-policy" in filtered.headers

    detail = await client.get(f"/companies/{nvidia_id}/view")
    assert detail.status_code == 200
    assert "NVIDIA Corporation" in detail.text
    assert "98,79" in detail.text
    assert "87,00" in detail.text
    assert "5,72 T" in detail.text
    assert "Forte" in detail.text
    assert "Exploitable" in detail.text
    assert "Fondamentaux" in detail.text
    assert ">Quality<" not in detail.text
    assert "La couverture de l’analyse d’opportunité est sous le seuil requis pour le classement." in detail.text
    assert "Opportunity coverage is below ranking threshold." not in detail.text
    assert "Untranslated custom warning." in detail.text
    assert "SMA200" in detail.text
    assert "price-chart" in detail.text
    assert "volume-chart" in detail.text
    assert "188.5" in detail.text
    assert '"sma20"' in detail.text
    assert "/static/vendor/chart.umd.min.js" in detail.text
    assert "/static/vendor/mermaid.min.js" in detail.text
    assert 'type="application/json"' in detail.text
    assert "mermaid.initialize" not in detail.text
    _assert_local_html(detail)
    assert settings.jwt_secret_key not in detail.text

    core_page = await client.get(f"/companies/{core_id}/view")
    assert core_page.status_code == 200
    assert "CoreWeave, Inc." in core_page.text
    assert "Analysé" in core_page.text
    assert "NVDA" in core_page.text

    discovery = await client.get("/discovery")
    assert discovery.status_code == 200
    assert "Acer" in discovery.text
    _assert_local_html(discovery)
    assert "OpenAI" in discovery.text
    assert "Partielle" in discovery.text
    assert "Partenaire" in discovery.text
    assert "CoreWeave" in discovery.text
    assert "Importée" in discovery.text
    partial = await client.get("/discovery", headers={"HX-Request": "true"}, params={"verification_status": "PARTIAL"})
    assert "Acer" in partial.text
    assert "OpenAI" in partial.text

    admin = await client.get("/admin/view")
    assert admin.status_code == 200
    assert "Postgres" in admin.text
    assert 'hx-get="/admin/view"' in admin.text
    _assert_local_html(admin)
    assert "Redis" in admin.text
    assert "Expansion de découverte" in admin.text
    assert "En attente" in admin.text
    assert "Ouvrir Netdata" in admin.text
    assert "http://192.168.1.116:19999" in admin.text
    assert settings.postgres_password not in admin.text
    refreshed = await client.get("/admin/view", headers={"HX-Request": "true"})
    assert "En attente" in refreshed.text
    assert "queued" not in refreshed.text
    assert "content-security-policy" in refreshed.headers
    assert settings.postgres_password not in refreshed.text

    api = await client.post(
        "/auth/login",
        data={"username": "pages@example.com", "password": "password123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    raw = await client.get(
        f"/companies/{nvidia_id}",
        headers={"Authorization": f"Bearer {api.json()['access_token']}"},
    )
    assert raw.status_code == 200
    assert raw.json()["universe_status"] == "WATCHED"
    assert raw.json()["discovery_source"] == "NASDAQ100"
    assert translate_warning("Totally unknown warning.") == "Totally unknown warning."


async def test_vendor_assets_cache_and_swagger(client):
    expected = {
        "/static/vendor/htmx.min.js": "htmx",
        "/static/vendor/chart.umd.min.js": "Chart.js v4.4.6",
        "/static/vendor/mermaid.min.js": 'globalThis["mermaid"]',
    }
    for path, marker in expected.items():
        asset = await client.get(path)
        assert asset.status_code == 200
        assert marker in asset.text
        cache = asset.headers["cache-control"]
        assert "public" in cache
        assert "max-age=31536000" in cache
        assert "immutable" in cache
        for host in _CDN:
            assert host not in asset.text

    stylesheet = await client.get("/static/css/sentinel.css")
    assert stylesheet.status_code == 200
    assert "max-age=3600" in stylesheet.headers["cache-control"]
    assert "immutable" not in stylesheet.headers["cache-control"]

    docs = await client.get("/docs")
    assert docs.status_code == 200
    assert "swagger-ui" in docs.text
    assert "content-security-policy" not in docs.headers
    spec = await client.get("/openapi.json")
    assert spec.status_code == 200
    assert spec.json()["info"]["title"] == "Sentinel API"
    assert "content-security-policy" not in spec.headers
