"""Read stored documents. This does not fetch anything."""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.intelligence import DocumentCompany, ExternalDocument, ExternalSource, IntelligenceEvent


async def company_news(
    session: AsyncSession,
    company_id: int,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    source: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    statement = (
        select(ExternalDocument, ExternalSource, DocumentCompany)
        .join(DocumentCompany, DocumentCompany.document_id == ExternalDocument.id)
        .join(ExternalSource, ExternalSource.id == ExternalDocument.source_id)
        .where(DocumentCompany.company_id == company_id)
        .order_by(ExternalDocument.published_at.desc(), ExternalDocument.id.desc())
        .offset(offset)
        .limit(limit)
    )
    if date_from is not None:
        statement = statement.where(ExternalDocument.published_at >= datetime.combine(date_from, datetime.min.time(), timezone.utc))
    if date_to is not None:
        statement = statement.where(
            ExternalDocument.published_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time(), timezone.utc)
        )
    if source:
        statement = statement.where(ExternalSource.name == source)
    rows = (await session.execute(statement)).all()
    return [
        {
            "document_id": document.id,
            "source_id": source_row.id,
            "source_name": source_row.name,
            "title": document.title,
            "url": document.url,
            "canonical_url": document.canonical_url,
            "published_at": document.published_at,
            "document_type": document.document_type,
            "summary": document.summary,
            "content_hash": document.content_hash,
            "relation_type": link.relation_type,
            "match_method": link.match_method,
            "confidence": link.confidence,
        }
        for document, source_row, link in rows
    ]


async def company_events(
    session: AsyncSession,
    company_id: int,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    from app.models.intelligence import EventCompany

    statement = (
        select(IntelligenceEvent, EventCompany)
        .join(EventCompany, EventCompany.event_id == IntelligenceEvent.id)
        .where(EventCompany.company_id == company_id)
        .order_by(IntelligenceEvent.event_date.desc(), IntelligenceEvent.id.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = (await session.execute(statement)).all()
    return [
        {
            "id": event.id,
            "event_type": event.event_type,
            "event_date": event.event_date,
            "title": event.title,
            "description": event.description,
            "importance": event.importance,
            "confidence": event.confidence,
            "source_document_id": event.source_document_id,
            "role": link.role,
            "role_confidence": link.confidence,
        }
        for event, link in rows
    ]


async def intelligence_counts(session: AsyncSession, now: datetime | None = None) -> dict:
    moment = now or datetime.now(timezone.utc)
    day_ago = moment - timedelta(hours=24)
    sources_active = int(
        (await session.execute(select(func.count()).select_from(ExternalSource).where(ExternalSource.is_active.is_(True)))).scalar_one()
    )
    documents_total = int((await session.execute(select(func.count()).select_from(ExternalDocument))).scalar_one())
    documents_last_24h = int(
        (
            await session.execute(
                select(func.count()).select_from(ExternalDocument).where(ExternalDocument.created_at >= day_ago)
            )
        ).scalar_one()
    )
    events_total = int((await session.execute(select(func.count()).select_from(IntelligenceEvent))).scalar_one())
    events_last_24h = int(
        (
            await session.execute(
                select(func.count()).select_from(IntelligenceEvent).where(IntelligenceEvent.created_at >= day_ago)
            )
        ).scalar_one()
    )
    last_success = await session.scalar(select(func.max(ExternalSource.last_success_at)))
    failed = int(
        (
            await session.execute(
                select(func.count())
                .select_from(ExternalSource)
                .where(
                    ExternalSource.last_error_at.is_not(None),
                    (ExternalSource.last_success_at.is_(None)) | (ExternalSource.last_error_at > ExternalSource.last_success_at),
                )
            )
        ).scalar_one()
    )
    return {
        "sources_active": sources_active,
        "documents_total": documents_total,
        "documents_last_24h": documents_last_24h,
        "events_total": events_total,
        "events_last_24h": events_last_24h,
        "last_success": last_success,
        "failed_sources": failed,
    }
