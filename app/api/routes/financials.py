from collections.abc import AsyncIterator
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.company import Company
from app.models.financial_metric import FinancialMetric
from app.schemas.financial import FinancialMetricRead, SecSyncRead
from app.services.sec.client import SecClient
from app.services.sec.errors import MissingSecCik, SecClientError, SecCompanyFactsNotFound
from app.services.sec.sync import sync_sec_financials

router = APIRouter(
    prefix="/companies",
    tags=["financials"],
    dependencies=[Depends(get_current_user)],
)

FiscalPeriod = Literal["FY", "Q1", "Q2", "Q3", "Q4"]


async def get_sec_client() -> AsyncIterator[SecClient]:
    client = SecClient(
        user_agent=settings.sec_user_agent,
        timeout=settings.sec_timeout_seconds,
        max_retries=settings.sec_max_retries,
        min_interval_seconds=settings.sec_min_interval_seconds,
    )
    try:
        yield client
    finally:
        await client.aclose()


def _period_order():
    return case(
        (FinancialMetric.fiscal_period == "FY", 5),
        (FinancialMetric.fiscal_period == "Q4", 4),
        (FinancialMetric.fiscal_period == "Q3", 3),
        (FinancialMetric.fiscal_period == "Q2", 2),
        (FinancialMetric.fiscal_period == "Q1", 1),
        else_=0,
    )


async def _get_company_or_404(db: AsyncSession, company_id: int) -> Company:
    company = await db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    return company


def _filtered_statement(
    company_id: int,
    *,
    fiscal_period: str | None,
    year_from: int | None,
    year_to: int | None,
):
    if year_from is not None and year_to is not None and year_from > year_to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="year_from must be less than or equal to year_to",
        )
    statement = select(FinancialMetric).where(FinancialMetric.company_id == company_id)
    if fiscal_period is not None:
        statement = statement.where(FinancialMetric.fiscal_period == fiscal_period)
    if year_from is not None:
        statement = statement.where(FinancialMetric.fiscal_year >= year_from)
    if year_to is not None:
        statement = statement.where(FinancialMetric.fiscal_year <= year_to)
    return statement.order_by(
        FinancialMetric.period_end.desc(),
        FinancialMetric.fiscal_year.desc(),
        _period_order().desc(),
        FinancialMetric.filed_at.desc(),
        FinancialMetric.id.desc(),
    )


@router.post("/{company_id}/financials/sec-sync", response_model=SecSyncRead)
async def sync_company_sec_financials(
    company_id: int,
    db: AsyncSession = Depends(get_db),
    client: SecClient = Depends(get_sec_client),
) -> SecSyncRead:
    company = await _get_company_or_404(db, company_id)
    try:
        result = await sync_sec_financials(db, company, client)
    except MissingSecCik:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Company has no SEC CIK")
    except SecCompanyFactsNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No SEC company facts for this CIK")
    except SecClientError:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="SEC EDGAR request failed")
    return SecSyncRead(
        company_id=result.company_id,
        ticker=result.ticker,
        periods_found=result.periods_found,
        created=result.created,
        updated=result.updated,
        skipped=result.skipped,
        deleted=result.deleted,
        warnings=list(result.warnings),
    )


@router.get("/{company_id}/financials/latest", response_model=FinancialMetricRead)
async def get_latest_financials(
    company_id: int,
    db: AsyncSession = Depends(get_db),
) -> FinancialMetric:
    await _get_company_or_404(db, company_id)
    statement = _filtered_statement(company_id, fiscal_period=None, year_from=None, year_to=None)
    metric = (await db.execute(statement.limit(1))).scalar_one_or_none()
    if metric is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No financial metrics for this company",
        )
    return metric


@router.get("/{company_id}/financials", response_model=list[FinancialMetricRead])
async def list_financials(
    company_id: int,
    fiscal_period: FiscalPeriod | None = Query(default=None),
    year_from: int | None = Query(default=None, ge=1900, le=2100),
    year_to: int | None = Query(default=None, ge=1900, le=2100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[FinancialMetric]:
    await _get_company_or_404(db, company_id)
    statement = _filtered_statement(
        company_id,
        fiscal_period=fiscal_period,
        year_from=year_from,
        year_to=year_to,
    )
    result = await db.execute(statement.offset(offset).limit(limit))
    return list(result.scalars().all())
