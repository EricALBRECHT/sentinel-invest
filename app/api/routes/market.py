from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.company import Company
from app.models.company_market_snapshot import CompanyMarketSnapshot
from app.models.market_price import MarketPrice
from app.schemas.market import MarketPriceRead, MarketSnapshotRead
from app.services.market.provider import MARKET_SOURCE

router = APIRouter(prefix="/companies", tags=["market"], dependencies=[Depends(get_current_user)])


@router.get("/{company_id}/market/history", response_model=list[MarketPriceRead])
async def market_history(
    company_id: int,
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    limit: int = Query(default=250, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[MarketPrice]:
    company = await db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    statement = select(MarketPrice).where(
        MarketPrice.company_id == company_id,
        MarketPrice.source == MARKET_SOURCE,
    )
    if date_from is not None:
        statement = statement.where(MarketPrice.trade_date >= date_from)
    if date_to is not None:
        statement = statement.where(MarketPrice.trade_date <= date_to)
    statement = statement.order_by(MarketPrice.trade_date.asc()).offset(offset).limit(limit)
    return list((await db.execute(statement)).scalars().all())


@router.get("/{company_id}/market/snapshot", response_model=MarketSnapshotRead)
async def market_snapshot(
    company_id: int,
    db: AsyncSession = Depends(get_db),
) -> CompanyMarketSnapshot:
    company = await db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    statement = select(CompanyMarketSnapshot).where(CompanyMarketSnapshot.company_id == company_id)
    snapshot = (await db.execute(statement)).scalar_one_or_none()
    if snapshot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No market snapshot for this company")
    return snapshot
