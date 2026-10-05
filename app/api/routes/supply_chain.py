from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.company import Company
from app.models.supply_chain import DiscoveredCompany
from app.schemas.discovery_expansion import DiscoveryGraphSummaryRead
from app.schemas.supply_chain import DiscoveredCompanyRead, PromotionRead, RelationshipRead, SupplyChainGraphRead
from app.services.discovery_expansion.summary import graph_summary
from app.services.discovery_verification import promote_candidate, reject_candidate, verify_candidate
from app.services.supply_chain.discovery import discover_candidates
from app.services.supply_chain.graph import get_company_graph
from app.services.supply_chain.relationships import list_relationships

router = APIRouter(tags=["supply-chain"], dependencies=[Depends(get_current_user)])


@router.get("/discovery/graph-summary", response_model=DiscoveryGraphSummaryRead)
async def discovery_graph_summary(db: AsyncSession = Depends(get_db)) -> dict:
    return await graph_summary(db)


@router.get("/companies/{company_id}/relationships", response_model=list[RelationshipRead])
async def company_relationships(
    company_id: int,
    relationship_type: str | None = Query(default=None, alias="type"),
    status_filter: str | None = Query(default=None, alias="status"),
    direction: str | None = Query(default=None, pattern="^(outbound|inbound)$"),
    confidence_min: int | None = Query(default=None, ge=0, le=100),
    db: AsyncSession = Depends(get_db),
) -> list:
    await _company(db, company_id)
    return await list_relationships(
        db,
        company_id,
        relationship_type=relationship_type,
        status=status_filter,
        direction=direction,
        confidence_min=confidence_min,
    )


@router.get("/companies/{company_id}/supply-chain", response_model=SupplyChainGraphRead)
async def company_supply_chain(
    company_id: int,
    depth: int = Query(default=1, ge=1, le=2),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await _company(db, company_id)
    return await get_company_graph(db, company_id, depth)


@router.get("/discovery/companies", response_model=list[DiscoveredCompanyRead])
async def discovered_companies(
    status_filter: str | None = Query(default=None, alias="status"),
    verification_status: str | None = Query(default=None),
    confidence_min: int | None = Query(default=None, ge=0, le=100),
    source_company_id: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> list:
    return await discover_candidates(
        db,
        source_company_id,
        status=status_filter,
        confidence_min=confidence_min,
        verification_status=verification_status,
    )


@router.get("/discovery/companies/{candidate_id}", response_model=DiscoveredCompanyRead)
async def discovered_company(candidate_id: int, db: AsyncSession = Depends(get_db)) -> DiscoveredCompany:
    return await _candidate(db, candidate_id)


@router.post("/discovery/companies/{candidate_id}/verify", response_model=DiscoveredCompanyRead)
async def verify_discovered_company(candidate_id: int, db: AsyncSession = Depends(get_db)) -> DiscoveredCompany:
    await _candidate(db, candidate_id)
    verified = await verify_candidate(db, candidate_id)
    if verified is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found")
    return verified


@router.post("/discovery/companies/{candidate_id}/promote", response_model=PromotionRead)
async def promote_discovered_company(candidate_id: int, db: AsyncSession = Depends(get_db)) -> PromotionRead:
    await _candidate(db, candidate_id)
    result = await promote_candidate(db, candidate_id)
    if result.detail == "Candidate was not found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result.detail)
    if not result.promoted and result.detail != "Already promoted":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=result.detail)
    return PromotionRead(
        promoted=result.promoted,
        company_id=result.company_id,
        relationships=result.relationships,
        detail=result.detail,
    )


@router.post("/discovery/companies/{candidate_id}/reject", response_model=DiscoveredCompanyRead)
async def reject_discovered_company(candidate_id: int, db: AsyncSession = Depends(get_db)) -> DiscoveredCompany:
    rejected = await reject_candidate(db, candidate_id)
    if rejected is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found")
    return rejected


async def _candidate(db: AsyncSession, candidate_id: int) -> DiscoveredCompany:
    row = await db.get(DiscoveredCompany, candidate_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found")
    return row


async def _company(db: AsyncSession, company_id: int) -> Company:
    company = await db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    return company
