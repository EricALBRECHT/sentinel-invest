from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.company import Company
from app.core.config import settings
from app.schemas.company import CompanyCreate, CompanyRead, CompanyUpdate
from app.schemas.discovery_expansion import DiscoveryStatusRead
from app.services.discovery_expansion.sources import linked_source_ids, sec_financial_eligible

router = APIRouter(
    prefix="/companies",
    tags=["companies"],
    dependencies=[Depends(get_current_user)],
)


def _conflict_from_integrity(exc: IntegrityError) -> HTTPException:
    message = str(exc.orig).lower()
    if "ticker" in message:
        detail = "A company with this ticker already exists"
    elif "isin" in message:
        detail = "A company with this ISIN already exists"
    elif "name" in message:
        detail = "A company with this name already exists"
    elif "sec_cik" in message:
        detail = "A company with this SEC CIK already exists"
    else:
        detail = "A company with these values already exists"
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


async def _ensure_unique(
    db: AsyncSession,
    *,
    name: str | None = None,
    ticker: str | None = None,
    isin: str | None = None,
    sec_cik: str | None = None,
    company_id: int | None = None,
) -> None:
    checks = (
        (Company.name, name, "A company with this name already exists"),
        (Company.ticker, ticker, "A company with this ticker already exists"),
        (Company.isin, isin, "A company with this ISIN already exists"),
        (Company.sec_cik, sec_cik, "A company with this SEC CIK already exists"),
    )
    for column, value, detail in checks:
        if value is None:
            continue
        statement = select(Company.id).where(column == value)
        if company_id is not None:
            statement = statement.where(Company.id != company_id)
        existing = (await db.execute(statement)).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _search_pattern(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@router.post("", response_model=CompanyRead, status_code=status.HTTP_201_CREATED)
async def create_company(
    payload: CompanyCreate,
    db: AsyncSession = Depends(get_db),
) -> Company:
    await _ensure_unique(
        db,
        name=payload.name,
        ticker=payload.ticker,
        isin=payload.isin,
        sec_cik=payload.sec_cik,
    )

    seen_at = datetime.now(timezone.utc)
    company = Company(
        name=payload.name,
        ticker=payload.ticker,
        isin=payload.isin,
        country=payload.country,
        exchange=payload.exchange,
        sector=payload.sector,
        industry=payload.industry,
        market_cap=payload.market_cap,
        pea_eligible=payload.pea_eligible,
        sec_cik=payload.sec_cik,
        market_symbol=payload.market_symbol,
        first_seen_at=seen_at,
        last_seen_at=seen_at,
    )
    db.add(company)

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _conflict_from_integrity(exc)

    await db.refresh(company)
    return company


@router.get("", response_model=list[CompanyRead])
async def list_companies(
    q: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[Company]:
    statement = select(Company).order_by(Company.id)
    query = q.strip() if q else ""
    if query:
        pattern = _search_pattern(query)
        statement = statement.where(
            or_(
                Company.name.ilike(pattern, escape="\\"),
                Company.ticker.ilike(pattern, escape="\\"),
            )
        )
    result = await db.execute(statement.offset(offset).limit(limit))
    return list(result.scalars().all())


@router.get("/{company_id}/discovery-status", response_model=DiscoveryStatusRead)
async def company_discovery_status(
    company_id: int,
    db: AsyncSession = Depends(get_db),
) -> DiscoveryStatusRead:
    company = await db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    return DiscoveryStatusRead(
        company_id=company.id,
        universe_status=company.universe_status,
        discovery_pipeline_status=company.discovery_pipeline_status,
        discovery_depth=company.discovery_depth,
        discovered_parent_company_id=company.discovered_parent_company_id,
        last_discovery_collection_at=company.last_discovery_collection_at,
        last_relationship_processing_at=company.last_relationship_processing_at,
        market_symbol=company.market_symbol,
        sec_sync_eligible=sec_financial_eligible(company),
        sources=len(await linked_source_ids(db, company.id)),
        max_depth=settings.discovery_max_depth,
    )


@router.get("/{company_id}", response_model=CompanyRead)
async def get_company(
    company_id: int,
    db: AsyncSession = Depends(get_db),
) -> Company:
    company = await db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    return company


@router.patch("/{company_id}", response_model=CompanyRead)
async def update_company(
    company_id: int,
    payload: CompanyUpdate,
    db: AsyncSession = Depends(get_db),
) -> Company:
    company = await db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")

    updates = payload.model_dump(exclude_unset=True)
    if updates:
        await _ensure_unique(
            db,
            name=updates.get("name"),
            ticker=updates.get("ticker"),
            isin=updates.get("isin"),
            sec_cik=updates.get("sec_cik"),
            company_id=company.id,
        )
        for field, value in updates.items():
            setattr(company, field, value)
        company.updated_at = datetime.now(timezone.utc)

        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise _conflict_from_integrity(exc)

        await db.refresh(company)

    return company
