"""Actualités & IA supervision MVP on the dashboard."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from app.models.ai_document_analysis import AiDocumentAnalysis
from app.models.company import Company
from app.models.company_score import CompanyScore
from app.models.intelligence import ExternalDocument, ExternalSource
from app.services.ai.prompts import PROMPT_VERSION
from datetime import date
from decimal import Decimal


async def _login(client, email="news@example.com", password="password123"):
    await client.post("/auth/register", json={"email": email, "password": password})
    return await client.post(
        "/login",
        data={"email": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


async def _seed_news(session_factory, *, with_analyses: bool = True):
    async with session_factory() as session:
        company = Company(name="NVIDIA Corporation", ticker="NVDA", is_active=True, universe_status="WATCHED")
        session.add(company)
        await session.flush()
        session.add(
            CompanyScore(
                company_id=company.id,
                score_date=date(2026, 10, 5),
                quality_score=Decimal("90"),
                overall_confidence_score=70,
                metrics_json={},
                confidence_json={},
                method_version="quality_v1",
            )
        )
        source = ExternalSource(
            name="NVIDIA Newsroom",
            source_type="RSS",
            base_url="https://example.test/rss",
            provider="rss",
            is_active=True,
            trust_level="HIGH",
            poll_interval_minutes=180,
            last_poll_at=datetime.now(timezone.utc) - timedelta(minutes=30),
            last_success_at=datetime.now(timezone.utc) - timedelta(minutes=30),
            metadata_json={},
        )
        stale = ExternalSource(
            name="Stale Feed",
            source_type="RSS",
            base_url="https://example.test/stale",
            provider="rss",
            is_active=True,
            trust_level="MEDIUM",
            poll_interval_minutes=60,
            last_poll_at=datetime.now(timezone.utc) - timedelta(days=3),
            last_success_at=datetime.now(timezone.utc) - timedelta(days=3),
            last_error_at=datetime.now(timezone.utc) - timedelta(hours=1),
            last_error_message="timeout",
            metadata_json={},
        )
        session.add_all([source, stale])
        await session.flush()
        now = datetime.now(timezone.utc)
        docs = []
        for index, (title, text, meta) in enumerate(
            (
                ("Full article", "NVIDIA and CoreWeave " * 80, {"content_source": "article_page", "article_chars": 4000}),
                ("Short teaser", "Teaser only […]", {"content_source": "rss_item", "rss_teaser_chars": 40}),
            ),
            start=1,
        ):
            doc = ExternalDocument(
                source_id=source.id,
                title=title,
                url=f"https://example.test/{index}",
                published_at=now - timedelta(hours=index),
                fetched_at=now - timedelta(hours=index),
                content_text=text,
                document_type="NEWS_ARTICLE",
                metadata_json=meta,
            )
            session.add(doc)
            docs.append(doc)
        await session.flush()
        if with_analyses:
            session.add(
                AiDocumentAnalysis(
                    document_id=docs[0].id,
                    model_name="Qwen2.5-1.5B-Instruct-Q4_K_M",
                    prompt_version=PROMPT_VERSION,
                    status="SUCCESS",
                    summary="NVIDIA remains the subject of the article.",
                    result_json={
                        "summary": "NVIDIA remains the subject of the article.",
                        "companies": [
                            {
                                "company_id": company.id,
                                "name": "NVIDIA Corporation",
                                "ticker": "NVDA",
                                "role": "SUBJECT",
                                "confidence": 90,
                                "evidence": "NVIDIA and CoreWeave",
                            }
                        ],
                        "events": [],
                        "relationships": [],
                        "strategic_signals": [],
                        "risks": [],
                        "analysis_confidence": 80,
                    },
                    analysis_confidence=80,
                    completed_at=now,
                    duration_ms=12000,
                    runtime_json={"tokens_input": 1200, "tokens_output": 140, "context_size": 4096},
                )
            )
            session.add(
                AiDocumentAnalysis(
                    document_id=docs[1].id,
                    model_name="Qwen2.5-1.5B-Instruct-Q4_K_M",
                    prompt_version=PROMPT_VERSION,
                    status="INVALID_OUTPUT",
                    error_message="companies[0].role=PARTNER requires explicit partnership evidence",
                    raw_output='{"companies":[{"role":"PARTNER"}]}',
                    completed_at=now,
                    duration_ms=8000,
                    runtime_json={"tokens_input": 400, "tokens_output": 90},
                )
            )
        await session.commit()
        return {
            "company_id": company.id,
            "source_id": source.id,
            "success_doc_id": docs[0].id,
            "invalid_doc_id": docs[1].id,
            "quality_before": Decimal("90"),
        }


async def test_dashboard_news_section_requires_auth(client):
    assert (await client.get("/dashboard")).status_code == 303
    hx = await client.get("/dashboard/fragments/news-sources", headers={"HX-Request": "true"})
    assert hx.status_code == 401
    assert hx.headers.get("HX-Redirect", "").startswith("/login")
    assert "Se connecter" not in (hx.text or "")


async def test_dashboard_renders_news_supervision(client, session_factory, monkeypatch):
    seed = await _seed_news(session_factory)
    monkeypatch.setattr(
        "app.services.gpu.workers.gpu_workers_for_page",
        lambda: [
            {
                "name": "sentinel-gpu-01",
                "online": True,
                "status": "online",
                "last_heartbeat": datetime.now(timezone.utc),
                "probe": {"model_loaded": True, "context_size": 4096},
            }
        ],
    )
    monkeypatch.setattr("app.jobs.queues.redis_connection", lambda: MagicMock())

    class _Queue:
        count = 0

    class _Registry:
        count = 0

        def get_job_ids(self):
            return []

    monkeypatch.setattr("rq.Queue", lambda *a, **k: _Queue())
    monkeypatch.setattr("rq.registry.StartedJobRegistry", lambda *a, **k: _Registry())

    await _login(client)
    page = await client.get("/dashboard")
    assert page.status_code == 200
    assert "Actualités &amp; IA" in page.text or "Actualités & IA" in page.text
    assert "NVIDIA Newsroom" in page.text
    assert "Documents récents" in page.text
    assert "IA / GPU" in page.text
    assert "Alertes" in page.text
    assert "Full article" in page.text
    assert "Short teaser" in page.text
    assert "Réussie" in page.text or "SUCCESS" in page.text or "Sortie invalide" in page.text
    assert "En ligne" in page.text
    assert f"/intelligence/documents/{seed['success_doc_id']}/view" in page.text

    sources = await client.get("/dashboard/fragments/news-sources")
    assert sources.status_code == 200
    assert "NVIDIA Newsroom" in sources.text
    assert "Stale Feed" in sources.text

    docs = await client.get("/dashboard/fragments/news-documents")
    assert docs.status_code == 200
    assert "Voir" in docs.text

    ai = await client.get("/dashboard/fragments/news-ai")
    assert ai.status_code == 200
    assert "SUCCESS 24 h" in ai.text
    assert "INVALID_OUTPUT 24 h" in ai.text
    assert "FAILED 24 h" in ai.text

    alerts = await client.get("/dashboard/fragments/news-alerts")
    assert alerts.status_code == 200
    assert "erreur" in alerts.text.lower() or "INVALID_OUTPUT" in alerts.text or "court" in alerts.text.lower()


async def test_gpu_offline_alert(client, session_factory, monkeypatch):
    await _seed_news(session_factory, with_analyses=False)
    monkeypatch.setattr(
        "app.services.gpu.workers.gpu_workers_for_page",
        lambda: [{"name": "sentinel-gpu-01", "online": False, "status": "offline", "probe": {}}],
    )
    monkeypatch.setattr("app.jobs.queues.redis_connection", lambda: MagicMock())

    class _Queue:
        count = 0

    class _Registry:
        count = 0

        def get_job_ids(self):
            return []

    monkeypatch.setattr("rq.Queue", lambda *a, **k: _Queue())
    monkeypatch.setattr("rq.registry.StartedJobRegistry", lambda *a, **k: _Registry())

    await _login(client, email="gpu-off@example.com")
    alerts = await client.get("/dashboard/fragments/news-alerts")
    assert "hors ligne" in alerts.text.lower()


async def test_document_detail_success_and_invalid(client, session_factory):
    seed = await _seed_news(session_factory)
    await _login(client, email="docview@example.com")

    success = await client.get(f"/intelligence/documents/{seed['success_doc_id']}/view")
    assert success.status_code == 200
    assert "Full article" in success.text
    assert "article_page" in success.text
    assert "NVIDIA Corporation" in success.text
    assert "SUBJECT" in success.text
    assert "Relancer l&#39;analyse" in success.text or "Relancer l'analyse" in success.text

    invalid = await client.get(f"/intelligence/documents/{seed['invalid_doc_id']}/view")
    assert invalid.status_code == 200
    assert "Sortie invalide" in invalid.text or "INVALID_OUTPUT" in invalid.text
    assert "requires explicit partnership" in invalid.text
    assert "raw-output" in invalid.text
    assert "PARTNER" in invalid.text


async def test_analyze_button_enqueues_existing_pipeline(client, session_factory, monkeypatch):
    seed = await _seed_news(session_factory, with_analyses=False)
    calls = []

    def _enqueue(document_id, force=False):
        calls.append({"document_id": document_id, "force": force})
        return {"job_id": f"ai-document-{document_id}-test", "queue": "intelligence", "enqueued": True, "status": "queued"}

    monkeypatch.setattr("app.jobs.queues.enqueue_ai_document_analysis", _enqueue)
    monkeypatch.setattr("app.jobs.queues.find_active_ai_document_job", lambda document_id: None)

    await _login(client, email="enqueue@example.com")
    response = await client.post(
        f"/intelligence/documents/{seed['success_doc_id']}/ai-analyze",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert calls == [{"document_id": seed["success_doc_id"], "force": False}]
    assert "ai-document-" in response.text
    assert "disabled" in response.text or "queued" in response.text.lower() or "Job" in response.text


async def test_analyze_no_double_enqueue_when_active(client, session_factory, monkeypatch):
    seed = await _seed_news(session_factory, with_analyses=False)
    active = MagicMock()
    active.id = "ai-document-active"
    active.get_status = MagicMock(return_value="started")
    calls = []

    def _enqueue(document_id, force=False):
        calls.append(document_id)
        return {"job_id": active.id, "queue": "intelligence", "enqueued": False, "status": "started"}

    monkeypatch.setattr("app.jobs.queues.find_active_ai_document_job", lambda document_id: active)
    monkeypatch.setattr("app.jobs.queues.enqueue_ai_document_analysis", _enqueue)

    await _login(client, email="activejob@example.com")
    page = await client.get(f"/intelligence/documents/{seed['success_doc_id']}/view")
    assert page.status_code == 200
    assert "disabled" in page.text
    assert "ai-document-active" in page.text

    await client.post(f"/intelligence/documents/{seed['success_doc_id']}/ai-analyze", headers={"HX-Request": "true"})
    # Route still calls enqueue; pipeline itself dedupes — ensure force not invented.
    assert calls == [seed["success_doc_id"]]


async def test_analyze_does_not_change_financial_scores(client, session_factory, monkeypatch):
    seed = await _seed_news(session_factory)
    monkeypatch.setattr(
        "app.jobs.queues.enqueue_ai_document_analysis",
        lambda document_id, force=False: {
            "job_id": "ai-document-x",
            "enqueued": True,
            "status": "queued",
        },
    )
    monkeypatch.setattr("app.jobs.queues.find_active_ai_document_job", lambda document_id: None)
    await _login(client, email="scores@example.com")
    await client.post(f"/intelligence/documents/{seed['success_doc_id']}/ai-analyze")
    async with session_factory() as session:
        from sqlalchemy import select

        row = await session.scalar(select(CompanyScore).where(CompanyScore.company_id == seed["company_id"]))
        assert row is not None
        assert row.quality_score == seed["quality_before"]


async def test_news_htmx_expired_session_keeps_hx_redirect(client):
    await _login(client, email="expire-news@example.com")
    client.cookies.set("sentinel_token", "not-a-jwt")
    response = await client.get("/dashboard/fragments/news-documents", headers={"HX-Request": "true"})
    assert response.status_code == 401
    assert response.headers["HX-Redirect"].startswith("/login")
    assert "Se connecter" not in (response.text or "")
