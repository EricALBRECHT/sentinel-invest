from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.company import Company
from app.models.intelligence import ExternalDocument, IntelligenceEvent
from app.schemas.intelligence import CompanyEventRead, DocumentRead, EventRead, NewsItemRead
from app.services.ai.service import get_latest_analysis, serialize_analysis
from app.services.intelligence.documents import company_events, company_news

router = APIRouter(tags=["intelligence"], dependencies=[Depends(get_current_user)])


@router.get("/companies/{company_id}/news", response_model=list[NewsItemRead])
async def company_news_route(
    company_id: int,
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    source: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    await _company(db, company_id)
    return await company_news(
        db,
        company_id,
        date_from=date_from,
        date_to=date_to,
        source=source,
        limit=limit,
        offset=offset,
    )


@router.get("/companies/{company_id}/events", response_model=list[CompanyEventRead])
async def company_events_route(
    company_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    await _company(db, company_id)
    return await company_events(db, company_id, limit=limit, offset=offset)


@router.get("/intelligence/documents/{document_id}", response_model=DocumentRead)
async def document_detail(document_id: int, db: AsyncSession = Depends(get_db)) -> ExternalDocument:
    document = await db.get(ExternalDocument, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return document


@router.get("/intelligence/documents/{document_id}/ai-analysis")
async def document_ai_analysis(document_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    document = await db.get(ExternalDocument, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    row = await get_latest_analysis(db, document_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No AI analysis for this document")
    return serialize_analysis(row).model_dump()


@router.get("/intelligence/events/{event_id}", response_model=EventRead)
async def event_detail(event_id: int, db: AsyncSession = Depends(get_db)) -> IntelligenceEvent:
    event = await db.get(IntelligenceEvent, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    return event


async def _company(db: AsyncSession, company_id: int) -> Company:
    company = await db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    return company
