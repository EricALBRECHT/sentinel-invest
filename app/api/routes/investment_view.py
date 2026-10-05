from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.company import Company
from app.models.investment_view import InvestmentView
from app.schemas.investment_view import InvestmentViewRead
from app.services.investment_view.scoring import METHOD
from app.services.investment_view.service import recalculate_investment_view

router = APIRouter(prefix="/companies", tags=["investment-view"], dependencies=[Depends(get_current_user)])


@router.post("/{company_id}/investment-view/recalculate", response_model=InvestmentViewRead)
async def recalculate_view(
    company_id: int,
    db: AsyncSession = Depends(get_db),
) -> InvestmentView:
    await _company(db, company_id)
    row = await recalculate_investment_view(db, company_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    return row


@router.get("/{company_id}/investment-view/latest", response_model=InvestmentViewRead)
async def latest_view(
    company_id: int,
    db: AsyncSession = Depends(get_db),
) -> InvestmentView:
    await _company(db, company_id)
    row = await db.scalar(
        select(InvestmentView)
        .where(InvestmentView.company_id == company_id, InvestmentView.method == METHOD)
        .order_by(InvestmentView.as_of_date.desc(), InvestmentView.id.desc())
        .limit(1)
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No investment view for this company")
    return row


@router.get("/{company_id}/investment-view/history", response_model=list[InvestmentViewRead])
async def view_history(
    company_id: int,
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    method: str = Query(default=METHOD),
    db: AsyncSession = Depends(get_db),
) -> list[InvestmentView]:
    await _company(db, company_id)
    statement = select(InvestmentView).where(
        InvestmentView.company_id == company_id,
        InvestmentView.method == method,
    )
    if date_from is not None:
        statement = statement.where(InvestmentView.as_of_date >= date_from)
    if date_to is not None:
        statement = statement.where(InvestmentView.as_of_date <= date_to)
    statement = statement.order_by(InvestmentView.as_of_date.desc()).offset(offset).limit(limit)
    return list((await db.execute(statement)).scalars().all())


async def _company(db: AsyncSession, company_id: int) -> Company:
    company = await db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    return company
