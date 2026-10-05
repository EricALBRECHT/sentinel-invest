from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.company import Company
from app.models.technical_snapshot import TechnicalSnapshot
from app.schemas.technical import TechnicalChartPointRead, TechnicalSnapshotRead
from app.services.technical.service import TECHNICAL_METHOD, chart_data, recalculate_technical_snapshot

router = APIRouter(prefix="/companies", tags=["technical"], dependencies=[Depends(get_current_user)])


@router.post("/{company_id}/technical/recalculate", response_model=TechnicalSnapshotRead)
async def recalculate_technical(
    company_id: int,
    db: AsyncSession = Depends(get_db),
) -> TechnicalSnapshot:
    await _company(db, company_id)
    row = await recalculate_technical_snapshot(db, company_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No market history for this company")
    return row


@router.get("/{company_id}/technical/latest", response_model=TechnicalSnapshotRead)
async def latest_technical(
    company_id: int,
    db: AsyncSession = Depends(get_db),
) -> TechnicalSnapshot:
    await _company(db, company_id)
    row = await _latest(db, company_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No technical snapshot for this company")
    return row


@router.get("/{company_id}/technical/history", response_model=list[TechnicalSnapshotRead])
async def technical_history(
    company_id: int,
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    method: str = Query(default=TECHNICAL_METHOD),
    db: AsyncSession = Depends(get_db),
) -> list[TechnicalSnapshot]:
    await _company(db, company_id)
    statement = select(TechnicalSnapshot).where(
        TechnicalSnapshot.company_id == company_id,
        TechnicalSnapshot.method == method,
    )
    if date_from is not None:
        statement = statement.where(TechnicalSnapshot.as_of_date >= date_from)
    if date_to is not None:
        statement = statement.where(TechnicalSnapshot.as_of_date <= date_to)
    statement = statement.order_by(TechnicalSnapshot.as_of_date.desc()).offset(offset).limit(limit)
    return list((await db.execute(statement)).scalars().all())


@router.get("/{company_id}/technical/chart-data", response_model=list[TechnicalChartPointRead])
async def technical_chart(
    company_id: int,
    limit: int = Query(default=252, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    rows = await chart_data(db, company_id, limit)
    if rows is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    return rows


async def _company(db: AsyncSession, company_id: int) -> Company:
    company = await db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    return company


async def _latest(db: AsyncSession, company_id: int) -> TechnicalSnapshot | None:
    statement = (
        select(TechnicalSnapshot)
        .where(
            TechnicalSnapshot.company_id == company_id,
            TechnicalSnapshot.method == TECHNICAL_METHOD,
        )
        .order_by(TechnicalSnapshot.as_of_date.desc())
        .limit(1)
    )
    return (await db.execute(statement)).scalar_one_or_none()
