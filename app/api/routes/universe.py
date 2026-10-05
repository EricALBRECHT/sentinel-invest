from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.company import Company
from app.models.universe_membership import UniverseMembership
from app.schemas.universe import (
    PriorityRead,
    UniverseAdd,
    UniverseCompanyRead,
    UniverseImportRead,
    UniverseListRead,
    UniverseMembershipRead,
)
from app.services.universe.importers import import_rows, parse_import
from app.services.universe.manager import (
    CompanyNotFound,
    MembershipNotFound,
    add_company_to_universe,
    get_company_universes,
    list_universe,
    recalculate_universe_priority,
    remove_company_from_universe,
)
from app.services.universe.rules import UniverseRuleError

router = APIRouter(prefix="/universe", tags=["universe"], dependencies=[Depends(get_current_user)])
company_router = APIRouter(prefix="/companies", tags=["universe"], dependencies=[Depends(get_current_user)])
import_router = APIRouter(prefix="/admin/universe", tags=["universe"], dependencies=[Depends(get_current_user)])


def _rule_error(exc: UniverseRuleError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


async def _companies(session: AsyncSession, companies: list[Company]) -> list[UniverseCompanyRead]:
    if not companies:
        return []
    ids = [company.id for company in companies]
    statement = (
        select(UniverseMembership)
        .where(UniverseMembership.company_id.in_(ids), UniverseMembership.is_active.is_(True))
        .order_by(UniverseMembership.universe_name)
    )
    grouped: dict[int, list[UniverseMembership]] = {company_id: [] for company_id in ids}
    for membership in (await session.execute(statement)).scalars():
        grouped[membership.company_id].append(membership)
    return [
        UniverseCompanyRead(
            id=company.id,
            ticker=company.ticker,
            name=company.name,
            universe_status=company.universe_status,
            universe_priority=company.universe_priority,
            discovery_source=company.discovery_source,
            is_active=company.is_active,
            pea_eligible=company.pea_eligible,
            memberships=[UniverseMembershipRead.model_validate(item) for item in grouped[company.id]],
        )
        for company in companies
    ]


@router.get("", response_model=UniverseListRead)
async def get_universe(
    status_filter: str | None = Query(default=None, alias="status"),
    source: str | None = Query(default=None),
    universe_name: str | None = Query(default=None),
    active: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> UniverseListRead:
    try:
        companies = await list_universe(
            db,
            universe_name=universe_name,
            status=status_filter,
            source=source,
            active=active,
            limit=limit,
            offset=offset,
        )
    except UniverseRuleError as exc:
        raise _rule_error(exc)
    return UniverseListRead(items=await _companies(db, companies), limit=limit, offset=offset)


@router.get("/{universe_name}", response_model=UniverseListRead)
async def get_named_universe(
    universe_name: str,
    status_filter: str | None = Query(default=None, alias="status"),
    source: str | None = Query(default=None),
    active: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> UniverseListRead:
    try:
        companies = await list_universe(
            db,
            universe_name=universe_name,
            status=status_filter,
            source=source,
            active=active,
            limit=limit,
            offset=offset,
        )
    except UniverseRuleError as exc:
        raise _rule_error(exc)
    return UniverseListRead(items=await _companies(db, companies), limit=limit, offset=offset)


@router.post("/companies/{company_id}", response_model=UniverseMembershipRead)
async def add_member(
    company_id: int,
    payload: UniverseAdd,
    db: AsyncSession = Depends(get_db),
) -> UniverseMembershipRead:
    try:
        membership, created = await add_company_to_universe(
            db,
            company_id,
            payload.universe_name,
            payload.source,
            universe_status=payload.universe_status,
            discovery_reason=payload.discovery_reason,
            metadata_json=payload.metadata_json,
        )
    except CompanyNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    except UniverseRuleError as exc:
        raise _rule_error(exc)
    body = UniverseMembershipRead.model_validate(membership).model_dump(mode="json")
    code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return JSONResponse(status_code=code, content=body)


@router.delete("/companies/{company_id}", response_model=UniverseMembershipRead)
async def remove_member(
    company_id: int,
    universe_name: str = Query(min_length=1),
    source: str = Query(min_length=1),
    db: AsyncSession = Depends(get_db),
) -> UniverseMembership:
    try:
        return await remove_company_from_universe(db, company_id, universe_name, source)
    except CompanyNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    except MembershipNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Active universe membership not found")
    except UniverseRuleError as exc:
        raise _rule_error(exc)


@company_router.get("/{company_id}/universes", response_model=list[UniverseMembershipRead])
async def company_universes(
    company_id: int,
    active: bool | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[UniverseMembership]:
    try:
        return await get_company_universes(db, company_id, active=active)
    except CompanyNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")


@company_router.post("/{company_id}/universe/recalculate-priority", response_model=PriorityRead)
async def recalculate_priority(company_id: int, db: AsyncSession = Depends(get_db)) -> PriorityRead:
    company = await db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    parts = await recalculate_universe_priority(db, company)
    await db.commit()
    await db.refresh(company)
    return PriorityRead(
        company_id=company.id,
        ticker=company.ticker,
        universe_priority=company.universe_priority,
        parts=parts,
    )


@import_router.post("/import", response_model=UniverseImportRead)
async def import_universe(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    body = await request.body()
    try:
        rows = parse_import(body, request.headers.get("content-type", ""))
        return await import_rows(db, rows)
    except UniverseRuleError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
