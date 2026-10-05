"""External documents stay offline. Feeds are fixtures and jobs use Redis db 15."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import inspect

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.jobs import scheduler
from app.jobs.queues import (
    enqueue_news_sync,
    enqueue_process_document,
    enqueue_source_poll,
    news_job_id,
    process_document_job_id,
    redis_connection,
    source_poll_job_id,
)
from app.jobs.worker import main as worker_main
from app.models.company import Company
from app.models.intelligence import CompanyAlias, DocumentCompany, ExternalDocument, ExternalSource, IntelligenceEvent
from app.services.intelligence.company_matching import CompanyIdentity, match_companies
from app.services.intelligence.events import extract_events
from app.services.intelligence.ingestion import ingest_source
from app.services.intelligence.providers.base import FetchBatch, NormalizedDocument
from app.services.intelligence.providers.http import HttpText
from app.services.intelligence.providers.robots import robots_policy
from app.services.intelligence.providers.rss import RssAtomProvider, parse_feed
from app.services.intelligence.providers.text import canonical_url, plain_text
from app.services.intelligence.schedule import interval_hours
from app.services.intelligence.sources import V1_CATALOG, create_source, register_v1_catalog
from tests.test_quality_score import _headers

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def job_redis(monkeypatch):
    monkeypatch.setattr(settings, "redis_db", 15)
    connection = redis_connection()
    connection.flushdb()
    yield connection
    connection.flushdb()
    connection.close()


class MemoryProvider:
    provider_name = "fake"

    def __init__(self, documents: list[NormalizedDocument]) -> None:
        self.documents = documents
        self.calls = 0

    async def fetch_since(self, source, since: datetime) -> FetchBatch:
        self.calls += 1
        kept = [item for item in self.documents if item.published_at is None or item.published_at >= since]
        return FetchBatch(kept)

    def normalize_document(self, raw: dict) -> NormalizedDocument:
        return raw

    async def health_check(self, source) -> bool:
        return True


def _doc(**overrides) -> NormalizedDocument:
    payload = dict(
        external_id="ext-1",
        url="https://example.com/story?utm_source=x",
        title="NVIDIA announced a new product",
        published_at=NOW,
        language="en",
        author="Desk",
        summary="NVIDIA announced a new product",
        content_text="NVIDIA announced a new product in its newsroom.",
        document_type="PRESS_RELEASE",
        metadata={"retention": "text_only_v1"},
    )
    payload.update(overrides)
    return NormalizedDocument(**payload)


def test_matching_accepts_ticker_and_legal_name_but_not_a_bare_common_word():
    nvidia = CompanyIdentity(1, "NVIDIA Corporation", "NVDA", (("NVIDIA", "COMMON_NAME"),))
    apple = CompanyIdentity(2, "Apple", "AAPL", (("Apple", "COMMON_NAME"), ("Apple Inc.", "LEGAL_NAME")))
    stm = CompanyIdentity(3, "STMicroelectronics", "STM", (("STMicroelectronics N.V.", "LEGAL_NAME"),))
    assert match_companies("Apple announced a new chip", [apple]) == []
    assert match_companies("I like apples and apple pie", [apple]) == []
    assert match_companies("AAPL updated its forecast", [apple])[0].match_method == "TICKER"
    assert match_companies("Apple Inc. updated its forecast", [apple])[0].match_method == "COMPANY_NAME"
    nvidia_hit = match_companies("NVIDIA reported earnings", [nvidia])[0]
    assert nvidia_hit.company_id == 1
    assert nvidia_hit.confidence >= 75
    assert match_companies("stm inside a word", [stm]) == []
    assert match_companies("STM filed a report", [stm])[0].match_method == "TICKER"


def test_events_use_explainable_importance():
    product = extract_events("The company announced a new product")
    factory = extract_events("The group approved a $10B fab investment")
    cancelled = extract_events("A major customer cancels contract")
    bankruptcy = extract_events("The issuer made a bankruptcy filing")
    factories = extract_events("How NVIDIA AI factories maximize return")
    assert [(item.event_type, item.importance) for item in product] == [("PRODUCT_LAUNCH", "LOW")]
    assert any(item.event_type == "NEW_FACTORY" and item.importance == "HIGH" for item in factory)
    assert any(item.event_type == "CONTRACT" and item.importance == "HIGH" for item in cancelled)
    assert any(item.event_type == "LEGAL" and item.importance == "CRITICAL" for item in bankruptcy)
    assert factories == []
    assert "Matched because of" in bankruptcy[0].description


def test_canonical_url_robots_and_feed_text_stay_plain():
    assert canonical_url("https://Example.com/a/b/?utm_source=x&id=1#frag") == "https://example.com/a/b?id=1"
    assert plain_text("<p>Hello <b>world</b></p>", 100) == "Hello world"
    allowed, delay = robots_policy("User-agent: *\nDisallow: /private\nCrawl-delay: 10\n", "Sentinel", "https://news.example/releases.xml")
    blocked, _ = robots_policy("User-agent: *\nDisallow: /cgi-bin\n", "Sentinel", "https://www.sec.gov/cgi-bin/browse-edgar")
    assert allowed is True
    assert delay == 10
    assert blocked is False
    items = parse_feed(
        """<?xml version="1.0"?>
        <rss><channel><title>Channel</title>
        <item><title>NVIDIA &amp; partners</title><link>https://example.com/one</link>
        <guid>abc</guid><pubDate>Mon, 05 Oct 2026 13:00:00 GMT</pubDate>
        <description><![CDATA[<p>Body</p>]]></description></item>
        </channel></rss>"""
    )
    assert items[0]["external_id"] == "abc"
    assert items[0]["title"] == "NVIDIA & partners"
    assert "<p>" in items[0]["content"]
    assert all("cgi-bin" not in item["base_url"] for item in V1_CATALOG)


async def test_rss_provider_respects_robots(monkeypatch):
    class Scripted:
        def __init__(self):
            self.calls = []

        def set_crawl_delay(self, url, seconds):
            return None

        async def get_text(self, url, user_agent=None):
            self.calls.append(url)
            if url.endswith("/robots.txt"):
                return HttpText(200, "User-agent: *\nDisallow: /releases.xml\n", url)
            return HttpText(200, "<rss></rss>", url)

    http = Scripted()
    provider = RssAtomProvider(http=http)
    source = ExternalSource(
        name="Blocked",
        source_type="RSS",
        base_url="https://news.example/releases.xml",
        provider="rss",
        trust_level="HIGH",
        poll_interval_minutes=60,
        metadata_json={},
    )
    batch = await provider.fetch_since(source, NOW - timedelta(days=7))
    assert batch.error == "robots.txt disallows this feed"
    assert batch.documents == []
    assert http.calls == ["https://news.example/robots.txt"]


async def test_ingestion_deduplicates_and_keeps_the_recent_window(session_factory):
    async with session_factory() as session:
        company = Company(name="NVIDIA Corporation", ticker="NVDA", universe_status="WATCHED")
        session.add(company)
        await session.commit()
        await session.refresh(company)
        source = await create_source(
            session,
            name="Fixture feed",
            source_type="RSS",
            base_url="https://example.com/feed.xml",
            provider="fake",
            trust_level="HIGH",
            poll_interval_minutes=60,
            metadata={"company_id": company.id, "document_type": "PRESS_RELEASE"},
        )
        recent = _doc()
        old = _doc(external_id="old", url="https://example.com/old", title="Old note", content_text="Old note", published_at=NOW - timedelta(days=30))
        provider = MemoryProvider([recent, old])
        first = await ingest_source(session, source.id, provider=provider, now=NOW)
        second = await ingest_source(session, source.id, provider=provider, now=NOW)
        forced = await ingest_source(session, source.id, provider=provider, now=NOW, force=True)
        other = await create_source(
            session,
            name="Other feed",
            source_type="NEWS",
            base_url="https://example.com/other.xml",
            provider="fake",
            metadata={"company_id": company.id},
        )
        url_copy = _doc(
            external_id="other-id",
            url="https://example.com/story?utm_campaign=y",
            title="Different headline",
            summary="Different headline",
            content_text="Different body",
        )
        hashed = _doc(external_id=None, url="https://example.com/different", title="Same words", content_text="Same words")
        await ingest_source(session, other.id, provider=MemoryProvider([url_copy]), now=NOW, force=True)
        await ingest_source(session, other.id, provider=MemoryProvider([hashed]), now=NOW, force=True)
        await ingest_source(session, source.id, provider=MemoryProvider([hashed]), now=NOW + timedelta(hours=2), force=True)
        count = await session.scalar(select(func.count()).select_from(ExternalDocument))
        stored = (await session.scalars(select(ExternalDocument))).all()
        links = (await session.scalars(select(DocumentCompany))).all()
        events = (await session.scalars(select(IntelligenceEvent))).all()

    assert first["status"] == "success"
    assert first["created"] == 1
    assert first["duplicates"] == 0
    assert second["status"] == "not_due"
    assert second["created"] == 0
    assert provider.calls == 2
    assert forced["duplicates"] == 1
    assert forced["created"] == 0
    assert count == 2
    assert {row.title for row in stored} == {"NVIDIA announced a new product", "Same words"}
    assert all("<" not in (row.content_text or "") for row in stored)
    assert all(row.metadata_json["retention"] == "text_only_v1" for row in stored)
    assert len(links) == 2
    assert links[0].relation_type == "SUBJECT"
    assert any(event.event_type == "PRODUCT_LAUNCH" and event.importance == "LOW" for event in events)


async def test_aliases_and_false_positive_do_not_link_apple(session_factory):
    async with session_factory() as session:
        nvidia = Company(name="NVIDIA Corporation", ticker="NVDA")
        apple = Company(name="Apple", ticker="AAPL")
        stm = Company(name="STMicroelectronics", ticker="STM")
        session.add_all([nvidia, apple, stm])
        await session.commit()
        registered = await register_v1_catalog(session)
        again = await register_v1_catalog(session)
        aliases = (await session.scalars(select(CompanyAlias).where(CompanyAlias.company_id == nvidia.id))).all()
        source = await create_source(
            session,
            name="Open wire",
            source_type="NEWS",
            base_url="https://example.com/wire.xml",
            provider="fake",
            metadata={},
        )
        apple_story = _doc(
            external_id="apple-1",
            url="https://example.com/apple",
            title="Apple announced a new chip",
            summary="Apple announced a new chip",
            content_text="Apple announced a new chip today.",
        )
        ticker_story = _doc(
            external_id="aapl-1",
            url="https://example.com/aapl",
            title="AAPL updated guidance",
            summary="AAPL updated guidance",
            content_text="AAPL updated guidance.",
        )
        await ingest_source(session, source.id, provider=MemoryProvider([apple_story, ticker_story]), now=NOW, force=True)
        apple_links = (
            await session.scalars(select(DocumentCompany).where(DocumentCompany.company_id == apple.id))
        ).all()

    assert registered["linked"] == 4
    assert registered["created"] == 4
    assert again["created"] == 0
    assert {alias.alias for alias in aliases} >= {"NVDA", "NVIDIA", "NVIDIA Corporation"}
    assert len(apple_links) == 1
    assert apple_links[0].match_method == "TICKER"


def test_news_jobs_and_scheduler_intervals(job_redis, monkeypatch):
    first = enqueue_news_sync(4, True)
    second = enqueue_news_sync(4, False)
    poll = enqueue_source_poll(3)
    process = enqueue_process_document(8)
    assert first["job_id"] == news_job_id(4) == "news-sync-company-4"
    assert first["queue"] == "intelligence"
    assert first["enqueued"] is True
    assert second["enqueued"] is False
    assert poll["job_id"] == source_poll_job_id(3)
    assert process["job_id"] == process_document_job_id(8)
    assert interval_hours("PORTFOLIO") == 2
    assert interval_hours("DEEP_ANALYSIS") == 4
    assert interval_hours("WATCHED") == 12
    assert interval_hours("DISCOVERED") == 24

    def fake():
        return {"selected": 0, "enqueued": 0, "already_active": 0, "job_ids": []}

    monkeypatch.setattr(scheduler, "enqueue_due_news_syncs", fake)
    assert scheduler.run_intelligence_scan()["enqueued"] == 0
    source = inspect.getsource(scheduler)
    assert "run_intelligence_scan" in source
    assert "fetch_since" not in source
    worker_source = inspect.getsource(worker_main)
    assert worker_source.index("QUEUE_ANALYSIS") < worker_source.index("QUEUE_INTELLIGENCE")
    assert worker_source.index("QUEUE_INTELLIGENCE") < worker_source.index("QUEUE_SEC")


async def test_news_routes_jwt_filters_and_admin_counts(client, session_factory):
    assert (await client.get("/companies/1/news")).status_code == 401
    assert (await client.get("/companies/1/events")).status_code == 401
    assert (await client.get("/intelligence/documents/1")).status_code == 401
    assert (await client.get("/intelligence/events/1")).status_code == 401
    assert (await client.post("/admin/jobs/news-sync/1")).status_code == 401
    headers = await _headers(client, email="news@example.com")
    created = await client.post(
        "/companies",
        json={"name": "NVIDIA Corporation", "ticker": "NVDA", "universe_status": "WATCHED"},
        headers=headers,
    )
    company_id = created.json()["id"]
    async with session_factory() as session:
        source = await create_source(
            session,
            name="Route feed",
            source_type="COMPANY_IR",
            base_url="https://example.com/nvda.xml",
            provider="fake",
            trust_level="HIGH",
            metadata={"company_id": company_id},
        )
        early = _doc(external_id="early", url="https://example.com/early", published_at=NOW - timedelta(days=3), title="NVIDIA partnership", content_text="NVIDIA partnership", summary="NVIDIA partnership")
        late = _doc(external_id="late", url="https://example.com/late", published_at=NOW - timedelta(days=1))
        result = await ingest_source(session, source.id, provider=MemoryProvider([early, late]), now=NOW, force=True)
        document_id = await session.scalar(select(ExternalDocument.id).order_by(ExternalDocument.id.desc()))
        event_id = await session.scalar(select(IntelligenceEvent.id).limit(1))
    news = await client.get(f"/companies/{company_id}/news", headers=headers)
    window = await client.get(
        f"/companies/{company_id}/news",
        params={"date_from": "2026-10-03", "date_to": "2026-10-04", "source": "Route feed"},
        headers=headers,
    )
    events = await client.get(f"/companies/{company_id}/events", headers=headers)
    detail = await client.get(f"/intelligence/documents/{document_id}", headers=headers)
    event = await client.get(f"/intelligence/events/{event_id}", headers=headers)
    admin = await client.get("/admin/status", headers=headers)
    body = admin.json()["intelligence"]

    assert result["created"] == 2
    assert news.status_code == 200
    assert [row["title"] for row in news.json()][0].startswith("NVIDIA")
    assert len(window.json()) == 1
    assert events.status_code == 200
    assert events.json()
    assert detail.status_code == 200
    assert detail.json()["metadata_json"]["retention"] == "text_only_v1"
    assert "<" not in (detail.json()["content_text"] or "")
    assert event.status_code == 200
    assert body["sources_active"] == 1
    assert body["documents_total"] == 2
    assert body["events_total"] >= 1
    assert body["failed_sources"] == 0
