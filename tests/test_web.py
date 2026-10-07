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
    assert 'data-sentinel-refresh' in dashboard.text
    assert 'hx-get="/dashboard/fragments/summary"' in dashboard.text
    assert "every 60s" in dashboard.text
    assert "every 10s" in dashboard.text
    assert "every 15s" in dashboard.text
    assert "every 30s" in dashboard.text
    assert "Actualisation automatique" in dashboard.text
    assert "Auto : activée" in dashboard.text
    assert "Dernière mise à jour" in dashboard.text
    assert "delay:400ms" in dashboard.text
    searched = await client.get("/dashboard", params={"q": "NVIDIA"})
    assert "NVIDIA Corporation" in searched.text
    assert "CoreWeave" not in searched.text
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
    assert 'data-sentinel-refresh' in admin.text
    assert 'hx-get="/admin/view/fragments/jobs"' in admin.text
    assert 'hx-get="/admin/view/fragments/bootstrap"' in admin.text
    assert 'hx-get="/admin/view/fragments/gpu"' in admin.text
    assert 'hx-get="/admin/view/fragments/overview"' in admin.text
    assert "sentinelRefresh from:body" in admin.text
    _assert_local_html(admin)
    assert "Redis" in admin.text
    assert "Expansion de découverte" in admin.text
    assert "Univers de marché" in admin.text
    assert "En attente" in admin.text
    assert "Ouvrir Netdata" in admin.text
    assert "http://192.168.1.116:19999" in admin.text
    assert settings.postgres_password not in admin.text
    refreshed = await client.get("/admin/view", headers={"HX-Request": "true"})
    assert "En attente" in refreshed.text
    assert "queued" not in refreshed.text
    assert "content-security-policy" in refreshed.headers
    assert settings.postgres_password not in refreshed.text
    assert "<html" not in refreshed.text.lower()
    assert "<nav" not in refreshed.text.lower()

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


def _assert_fragment(response):
    assert response.status_code == 200
    body = response.text.lower()
    assert "<html" not in body
    assert "<head" not in body
    assert "<nav" not in body
    assert "content-security-policy" in response.headers
    for host in _CDN:
        assert host not in response.text


async def test_dashboard_and_admin_live_fragments(client, session_factory, monkeypatch):
    from app.models.company import Company

    async with session_factory() as session:
        session.add(
            Company(
                name="NVIDIA Corporation",
                ticker="NVDA",
                exchange="NASDAQ",
                universe_status="WATCHED",
                universe_priority=90,
                discovery_source="NASDAQ100",
                is_active=True,
            )
        )
        session.add(
            Company(
                name="Apple Inc.",
                ticker="AAPL",
                exchange="NASDAQ",
                universe_status="SCREENED",
                universe_priority=40,
                discovery_source="SP500",
                is_active=True,
            )
        )
        await session.commit()

    calls = {"sec": 0, "market": 0, "tech": 0, "gpu": 0, "boot": 0}

    def _bump(key):
        def _inner(*args, **kwargs):
            calls[key] += 1
            return {"enqueued": True}

        return _inner

    async def _boot(*args, **kwargs):
        calls["boot"] += 1
        return {"ok": True}

    monkeypatch.setattr("app.jobs.queues.enqueue_sec_sync", _bump("sec"))
    monkeypatch.setattr("app.jobs.queues.enqueue_market_sync", _bump("market"))
    monkeypatch.setattr("app.jobs.queues.enqueue_technical", _bump("tech"))
    monkeypatch.setattr("app.jobs.queues.enqueue_ai_document_analysis", _bump("gpu"))
    monkeypatch.setattr("app.services.universe.bootstrap.bootstrap_universe_data", _boot)

    assert (await client.get("/dashboard/fragments/summary")).status_code == 303
    assert (await client.get("/dashboard/fragments/companies")).status_code == 303
    assert (await client.get("/admin/view/fragments/jobs")).status_code == 303

    await _login(client, email="live@example.com")

    summary = await client.get("/dashboard/fragments/summary")
    _assert_fragment(summary)
    assert 'id="dashboard-summary"' in summary.text
    assert 'hx-get="/dashboard/fragments/summary"' in summary.text
    assert 'hx-trigger="every 60s, sentinelRefresh from:body"' in summary.text
    assert 'hx-swap="outerHTML"' in summary.text
    assert "Sociétés actives" in summary.text

    companies = await client.get(
        "/dashboard/fragments/companies",
        params={"universe_status": "WATCHED", "q": "NVDA", "limit": 50, "offset": 0},
    )
    _assert_fragment(companies)
    assert 'id="company-table"' in companies.text
    assert "NVIDIA Corporation" in companies.text
    assert "Apple Inc." not in companies.text
    assert "universe_status=WATCHED" in companies.text
    assert "q=NVDA" in companies.text
    assert "offset=0" in companies.text
    assert 'hx-get="/dashboard/fragments/companies?' in companies.text
    assert "every 60s" in companies.text

    page2 = await client.get(
        "/dashboard/fragments/companies",
        params={"universe_status": "", "q": "", "limit": 1, "offset": 1},
    )
    _assert_fragment(page2)
    assert "offset=1" in page2.text
    assert "limit=1" in page2.text

    jobs = await client.get("/dashboard/fragments/jobs")
    _assert_fragment(jobs)
    assert 'hx-get="/dashboard/fragments/jobs"' in jobs.text
    assert "every 10s" in jobs.text

    bootstrap = await client.get("/dashboard/fragments/bootstrap")
    _assert_fragment(bootstrap)
    assert 'hx-get="/dashboard/fragments/bootstrap"' in bootstrap.text
    assert "every 15s" in bootstrap.text

    gpu = await client.get("/dashboard/fragments/gpu")
    _assert_fragment(gpu)
    assert 'hx-get="/dashboard/fragments/gpu"' in gpu.text
    assert "every 30s" in gpu.text

    admin_jobs = await client.get("/admin/view/fragments/jobs")
    _assert_fragment(admin_jobs)
    assert 'hx-get="/admin/view/fragments/jobs"' in admin_jobs.text
    assert "every 10s" in admin_jobs.text
    assert "Tâches" in admin_jobs.text

    admin_boot = await client.get("/admin/view/fragments/bootstrap")
    _assert_fragment(admin_boot)
    assert "every 15s" in admin_boot.text
    assert "Univers de marché" in admin_boot.text

    admin_gpu = await client.get("/admin/view/fragments/gpu")
    _assert_fragment(admin_gpu)
    assert "every 30s" in admin_gpu.text
    assert "Workers GPU" in admin_gpu.text

    admin_overview = await client.get("/admin/view/fragments/overview")
    _assert_fragment(admin_overview)
    assert "every 60s" in admin_overview.text
    assert "Postgres" in admin_overview.text

    dashboard = await client.get("/dashboard")
    assert 'data-sentinel-refresh' in dashboard.text
    assert "sentinelRefresh from:body" in dashboard.text
    js = await client.get("/static/js/sentinel.js")
    assert js.status_code == 200
    assert "sentinelRefresh" in js.text
    assert "data-sentinel-refresh" in js.text
    assert "Échec actualisation" in js.text
    for host in _CDN:
        assert host not in js.text
        assert host not in dashboard.text

    assert calls == {"sec": 0, "market": 0, "tech": 0, "gpu": 0, "boot": 0}


async def test_polling_intervals_centralized():
    from app.web import polling

    assert polling.POLL_SUMMARY_SECONDS == 60
    assert polling.POLL_COMPANIES_SECONDS == 60
    assert polling.POLL_BOOTSTRAP_SECONDS == 15
    assert polling.POLL_JOBS_SECONDS == 10
    assert polling.POLL_GPU_SECONDS == 30
    assert polling.POLL_ADMIN_OVERVIEW_SECONDS == 60
    assert polling.POLL_NEWS_SOURCES_SECONDS == 60
    assert polling.POLL_NEWS_DOCUMENTS_SECONDS == 45
    assert polling.POLL_NEWS_AI_SECONDS == 20
    assert polling.POLL_NEWS_ALERTS_SECONDS == 30
    assert polling.SEARCH_DELAY_MS == 400
    ctx = polling.poll_context()
    assert ctx["poll_summary"] == 60
    assert ctx["poll_news_sources"] == 60
    assert ctx["search_delay_ms"] == 400


_HX = {"HX-Request": "true"}

_FRAGMENT_PATHS = (
    "/dashboard/fragments/summary",
    "/dashboard/fragments/companies",
    "/dashboard/fragments/bootstrap",
    "/dashboard/fragments/jobs",
    "/dashboard/fragments/gpu",
    "/admin/view/fragments/jobs",
    "/admin/view/fragments/bootstrap",
    "/admin/view/fragments/gpu",
    "/admin/view/fragments/overview",
)


def _assert_htmx_login_redirect(response):
    assert response.status_code == 401
    assert response.headers.get("HX-Redirect", "").startswith("/login")
    body = response.text or ""
    assert "Se connecter" not in body
    assert 'name="password"' not in body
    assert "<form" not in body.lower()
    assert "Session expirée" not in body


async def test_unauthenticated_browser_redirects_to_login(client):
    response = await client.get("/dashboard", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


async def test_unauthenticated_htmx_fragment_uses_hx_redirect(client):
    for path in _FRAGMENT_PATHS:
        response = await client.get(path, headers=_HX, follow_redirects=False)
        _assert_htmx_login_redirect(response)
        # Fragment paths map next= to the parent page, not the fragment URL.
        location = response.headers["HX-Redirect"]
        assert "/fragments/" not in location
        if path.startswith("/dashboard"):
            assert "next=/dashboard" in location
        else:
            assert "next=/admin/view" in location


async def test_unauthenticated_htmx_does_not_return_login_html(client):
    """Regression: expired session must not inject login.html into fragment targets."""
    response = await client.get("/dashboard/fragments/summary", headers=_HX, follow_redirects=False)
    _assert_htmx_login_redirect(response)
    # Follow redirects would be wrong for HTMX — body must stay empty of login UI.
    assert len(response.content) == 0


async def test_authenticated_htmx_fragment_returns_partial(client):
    await _login(client, email="htmx-ok@example.com")
    response = await client.get("/dashboard/fragments/summary", headers=_HX)
    assert response.status_code == 200
    assert "HX-Redirect" not in response.headers
    assert 'id="dashboard-summary"' in response.text
    assert "Se connecter" not in response.text


async def test_htmx_after_session_expiry_redirects_full_page(client):
    await _login(client, email="htmx-expire@example.com")
    ok = await client.get("/dashboard/fragments/jobs", headers=_HX)
    assert ok.status_code == 200
    assert "Tâches" in ok.text or "jobs" in ok.text.lower() or 'id="dashboard-jobs"' in ok.text or "hx-get" in ok.text

    client.cookies.set("sentinel_token", "not-a-jwt")
    expired = await client.get("/dashboard/fragments/jobs", headers=_HX, follow_redirects=False)
    _assert_htmx_login_redirect(expired)
    assert expired.headers["HX-Redirect"].startswith("/login")


async def test_jwt_access_token_expire_minutes_is_configured():
    """Document current session length — do not inflate to hide HTMX auth bugs."""
    assert settings.access_token_expire_minutes == 60
