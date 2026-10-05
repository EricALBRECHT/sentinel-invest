"""Fetch a source, keep new documents, link companies, and extract events.

Scores are not read or written here.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.company import Company
from app.models.company_sync_status import CompanySyncStatus
from app.models.intelligence import (
    CompanyAlias,
    CompanyExternalSource,
    DocumentCompany,
    EventCompany,
    ExternalDocument,
    ExternalSource,
    IntelligenceEvent,
)
from app.services.intelligence.company_matching import CompanyIdentity, match_companies
from app.services.intelligence.deduplication import document_identity, find_duplicate
from app.services.intelligence.events import extract_events
from app.services.intelligence.providers import provider_for
from app.services.intelligence.providers.base import NormalizedDocument
from app.services.intelligence.sources import NEWS_SYNC_SOURCE, ensure_aliases

_ACTIVE = "text_only_v1"


async def ingest_source(
    session: AsyncSession,
    source_id: int,
    *,
    provider=None,
    now: datetime | None = None,
    force: bool = False,
) -> dict:
    source = await session.get(ExternalSource, source_id)
    if source is None:
        return _summary(source_id, "missing_source")
    if not source.is_active:
        return _summary(source_id, "inactive")
    moment = now or datetime.now(timezone.utc)
    if not force and _polled_recently(source, moment):
        return _summary(source_id, "not_due")
    since = _since(source, moment)
    source.last_poll_at = moment
    source.updated_at = moment
    chosen = provider if provider is not None else _provider(source.provider)
    if chosen is None:
        return await _fail(session, source, moment, "Unknown provider")
    try:
        batch = await chosen.fetch_since(source, since)
    except Exception as exc:
        return await _fail(session, source, moment, type(exc).__name__)
    if batch.error:
        return await _fail(session, source, moment, batch.error)
    created = 0
    duplicates = 0
    events = 0
    for document in batch.documents:
        outcome, event_count = await _store_document(session, source, document, moment)
        if outcome == "duplicate":
            duplicates += 1
        elif outcome == "created":
            created += 1
            events += event_count
    source.last_success_at = moment
    source.last_error_message = None
    source.updated_at = moment
    await session.commit()
    return _summary(source_id, "success", created=created, duplicates=duplicates, events=events)


async def sync_company_news(
    session: AsyncSession,
    company_id: int,
    *,
    provider=None,
    now: datetime | None = None,
    force: bool = False,
) -> dict:
    company = await session.get(Company, company_id)
    if company is None:
        return {"company_id": company_id, "status": "missing_company", "created": 0, "duplicates": 0, "events": 0}
    await ensure_aliases(session, company)
    linked = set(
        (
            await session.scalars(
                select(CompanyExternalSource.source_id).where(CompanyExternalSource.company_id == company_id)
            )
        ).all()
    )
    sources = [
        source
        for source in (await session.scalars(select(ExternalSource).where(ExternalSource.is_active.is_(True)))).all()
        if source.id in linked or _source_company_id(source) == company_id
    ]
    if not sources:
        return {"company_id": company_id, "status": "no_sources", "created": 0, "duplicates": 0, "events": 0, "sources": 0}
    created = duplicates = events = errors = 0
    for source in sources:
        result = await ingest_source(session, source.id, provider=provider, now=now, force=force)
        created += int(result.get("created") or 0)
        duplicates += int(result.get("duplicates") or 0)
        events += int(result.get("events") or 0)
        if result.get("status") == "error":
            errors += 1
    status = "error" if errors and errors == len(sources) else "success"
    await _remember_company(session, company_id, status, created, duplicates, events, now)
    return {
        "company_id": company_id,
        "status": status,
        "created": created,
        "duplicates": duplicates,
        "events": events,
        "sources": len(sources),
        "errors": errors,
    }


async def process_document(session: AsyncSession, document_id: int) -> dict:
    document = await session.get(ExternalDocument, document_id)
    if document is None:
        return {"document_id": document_id, "status": "missing_document", "companies": 0, "events": 0}
    source = await session.get(ExternalSource, document.source_id)
    await _link_companies(session, document, source)
    links = await _linked_company_ids(session, document.id)
    events = await _link_events(session, document, links)
    await session.commit()
    return {"document_id": document_id, "status": "success", "companies": len(links), "events": events}


async def _store_document(
    session: AsyncSession,
    source: ExternalSource,
    document: NormalizedDocument,
    moment: datetime,
) -> tuple[str, int]:
    identity = document_identity(document.external_id, document.url, document.title, document.content_text)
    existing = await find_duplicate(
        session,
        source_id=source.id,
        external_id=identity["external_id"],
        url=document.url,
        title=document.title,
        content_text=document.content_text,
    )
    if existing is not None:
        return "duplicate", 0
    metadata = {
        "provider": source.provider,
        "source_name": source.name,
        "source_type": source.source_type,
        "trust_level": source.trust_level,
        "retention": _ACTIVE,
        "feed_url": source.base_url,
        **(document.metadata or {}),
    }
    row = ExternalDocument(
        source_id=source.id,
        external_id=identity["external_id"],
        url=(document.url or "")[:1000] or None,
        canonical_url=identity["canonical_url"],
        title=document.title,
        published_at=document.published_at,
        fetched_at=moment,
        language=document.language,
        author=document.author,
        summary=document.summary,
        content_text=document.content_text,
        content_hash=identity["content_hash"],
        document_type=document.document_type,
        metadata_json=metadata,
        created_at=moment,
        updated_at=moment,
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        return "duplicate", 0
    await _link_companies(session, row, source)
    links = await _linked_company_ids(session, row.id)
    event_count = await _link_events(session, row, links)
    return "created", event_count


async def _link_companies(session: AsyncSession, document: ExternalDocument, source: ExternalSource | None) -> int:
    text = "\n".join(part for part in (document.title, document.summary, document.content_text) if part)
    identities = await _identities(session)
    matches = {item.company_id: item for item in match_companies(text, identities)}
    owner_id = _source_company_id(source) if source is not None else None
    created = 0
    if owner_id is not None and owner_id not in matches:
        await _add_link(session, document.id, owner_id, "SUBJECT", "MANUAL", 100)
        created += 1
    for company_id, match in matches.items():
        relation = "SUBJECT" if company_id == owner_id else "MENTION"
        confidence = max(match.confidence, 90) if company_id == owner_id else match.confidence
        added = await _add_link(session, document.id, company_id, relation, match.match_method, confidence)
        created += int(added)
    return created


async def _add_link(session, document_id: int, company_id: int, relation: str, method: str, confidence: int) -> bool:
    existing = await session.scalar(
        select(DocumentCompany).where(
            DocumentCompany.document_id == document_id,
            DocumentCompany.company_id == company_id,
            DocumentCompany.relation_type == relation,
        )
    )
    if existing is not None:
        if confidence > existing.confidence:
            existing.confidence = confidence
            existing.match_method = method
        return False
    session.add(
        DocumentCompany(
            document_id=document_id,
            company_id=company_id,
            relation_type=relation,
            match_method=method,
            confidence=confidence,
        )
    )
    await session.flush()
    return True


async def _link_events(session: AsyncSession, document: ExternalDocument, company_ids: list[tuple[int, str, int]]) -> int:
    text = "\n".join(part for part in (document.title, document.summary, document.content_text) if part)
    created = 0
    event_day = None if document.published_at is None else document.published_at.date()
    for extracted in extract_events(text):
        existing = await session.scalar(
            select(IntelligenceEvent).where(
                IntelligenceEvent.source_document_id == document.id,
                IntelligenceEvent.event_type == extracted.event_type,
            )
        )
        if existing is not None:
            continue
        event = IntelligenceEvent(
            event_type=extracted.event_type,
            event_date=event_day,
            title=extracted.title,
            description=extracted.description,
            importance=extracted.importance,
            confidence=extracted.confidence,
            source_document_id=document.id,
            created_at=document.fetched_at,
            updated_at=document.fetched_at,
        )
        session.add(event)
        await session.flush()
        for company_id, relation, confidence in company_ids:
            session.add(
                EventCompany(
                    event_id=event.id,
                    company_id=company_id,
                    role=relation,
                    confidence=min(confidence, extracted.confidence),
                )
            )
        created += 1
    if created:
        await session.flush()
    return created


async def _linked_company_ids(session: AsyncSession, document_id: int) -> list[tuple[int, str, int]]:
    rows = (
        await session.scalars(select(DocumentCompany).where(DocumentCompany.document_id == document_id))
    ).all()
    return [(row.company_id, row.relation_type, row.confidence) for row in rows]


async def _identities(session: AsyncSession) -> list[CompanyIdentity]:
    companies = (await session.scalars(select(Company).where(Company.is_active.is_(True)))).all()
    aliases = (await session.scalars(select(CompanyAlias).where(CompanyAlias.is_active.is_(True)))).all()
    grouped: dict[int, list[tuple[str, str]]] = {}
    for alias in aliases:
        grouped.setdefault(alias.company_id, []).append((alias.alias, alias.alias_type))
    return [
        CompanyIdentity(
            company_id=company.id,
            name=company.name,
            ticker=company.ticker,
            aliases=tuple(grouped.get(company.id, [])),
        )
        for company in companies
    ]


def _source_company_id(source: ExternalSource | None) -> int | None:
    if source is None:
        return None
    value = (source.metadata_json or {}).get("company_id")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _since(source: ExternalSource, moment: datetime) -> datetime:
    window_start = moment - timedelta(days=settings.intelligence_lookback_days)
    if source.last_success_at is None:
        return window_start
    success = source.last_success_at
    if success.tzinfo is None:
        success = success.replace(tzinfo=timezone.utc)
    return max(success, window_start)


def _polled_recently(source: ExternalSource, moment: datetime) -> bool:
    if source.last_poll_at is None:
        return False
    polled = source.last_poll_at
    if polled.tzinfo is None:
        polled = polled.replace(tzinfo=timezone.utc)
    return moment - polled < timedelta(minutes=source.poll_interval_minutes)


def _provider(name: str):
    try:
        return provider_for(name)
    except KeyError:
        return None


async def _fail(session: AsyncSession, source: ExternalSource, moment: datetime, message: str) -> dict:
    source.last_error_at = moment
    source.last_error_message = message[:500]
    source.updated_at = moment
    await session.commit()
    return _summary(source.id, "error", error=message[:500])


async def _remember_company(session, company_id: int, status: str, created: int, duplicates: int, events: int, now) -> None:
    moment = now or datetime.now(timezone.utc)
    row = await session.scalar(
        select(CompanySyncStatus).where(
            CompanySyncStatus.company_id == company_id,
            CompanySyncStatus.source == NEWS_SYNC_SOURCE,
        )
    )
    if row is None:
        row = CompanySyncStatus(company_id=company_id, source=NEWS_SYNC_SOURCE, consecutive_failures=0)
        session.add(row)
    row.last_attempt_at = moment
    row.last_result_json = {"created": created, "duplicates": duplicates, "events": events, "status": status}
    row.updated_at = moment
    if status == "success":
        row.last_success_at = moment
        row.consecutive_failures = 0
        row.last_error_message = None
    else:
        row.last_error_at = moment
        row.consecutive_failures = int(row.consecutive_failures or 0) + 1
        row.last_error_message = "Every linked source failed"
    await session.commit()


def _summary(source_id: int, status: str, created: int = 0, duplicates: int = 0, events: int = 0, error: str | None = None) -> dict:
    return {
        "source_id": source_id,
        "status": status,
        "created": created,
        "duplicates": duplicates,
        "events": events,
        "error": error,
    }
