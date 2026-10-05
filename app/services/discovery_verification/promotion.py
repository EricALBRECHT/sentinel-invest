"""Promote a verified candidate into a discovered company, then replay its documents."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.company import Company
from app.models.intelligence import CompanyAlias
from app.models.supply_chain import CompanyRelationship, DiscoveredCompany
from app.services.discovery_verification.matching import find_existing_company
from app.services.supply_chain.extraction import extract_relationships
from app.services.universe.manager import add_company_to_universe, recalculate_universe_priority


class PromotionResult:
    def __init__(self, promoted: bool, company_id: int | None, relationships: int, detail: str) -> None:
        self.promoted = promoted
        self.company_id = company_id
        self.relationships = relationships
        self.detail = detail


async def promote_candidate(session: AsyncSession, candidate_id: int) -> PromotionResult:
    row = await session.get(DiscoveredCompany, candidate_id)
    if row is None:
        return PromotionResult(False, None, 0, "Candidate was not found")
    if row.promoted_company_id is not None or row.status == "IMPORTED":
        return PromotionResult(True, row.promoted_company_id, 0, "Already promoted")
    if row.verification_status == "REJECTED" or row.status == "REJECTED":
        return PromotionResult(False, None, 0, "Candidate was rejected")
    if row.verification_status != "VERIFIED":
        return PromotionResult(False, None, 0, "Candidate is not verified")
    matches = await find_existing_company(session, row)
    if len(matches) > 1:
        row.verification_status = "PARTIAL"
        row.verification_confidence = 50
        row.verification_evidence_json = {
            "decision": "partial",
            "reason": "Several companies match this candidate",
            "possible_matches": [{"source": "COMPANY", "company_id": company.id, "name": company.name} for company in matches],
        }
        await session.commit()
        return PromotionResult(False, None, 0, "Identity is ambiguous")
    created = False
    if len(matches) == 1:
        company = matches[0]
    else:
        if not (row.ticker or "").strip():
            return PromotionResult(False, None, 0, "Private company has no ticker")
        parent = None
        if row.discovered_from_company_id is not None:
            parent = await session.get(Company, row.discovered_from_company_id)
        depth = 0 if parent is None else int(parent.discovery_depth or 0) + 1
        company = Company(
            name=(row.canonical_name or row.name)[:255],
            ticker=row.ticker.strip().upper(),
            isin=row.isin,
            country=row.country,
            exchange=row.exchange,
            sec_cik=row.sec_cik,
            universe_status="DISCOVERED",
            universe_priority=0,
            discovery_source="SUPPLY_CHAIN",
            discovery_reason=_reason(row),
            is_active=True,
            discovered_parent_company_id=None if parent is None else parent.id,
            discovery_depth=depth,
            discovery_pipeline_status=_pipeline_status(row.sec_cik, depth),
        )
        session.add(company)
        await session.flush()
        created = True
    await _aliases(session, company, row)
    await add_company_to_universe(
        session,
        company.id,
        "SUPPLY_CHAIN",
        "SUPPLY_CHAIN",
        universe_status=company.universe_status,
        discovery_reason=_reason(row) if created else None,
        commit=False,
    )
    if created:
        await recalculate_universe_priority(session, company)
    row.promoted_company_id = company.id
    row.status = "IMPORTED"
    row.updated_at = datetime.now(timezone.utc)
    document_ids = [int(item) for item in (row.document_ids or [])]
    await session.commit()
    for document_id in document_ids:
        await extract_relationships(session, document_id)
    relationship_ids = list(
        (
            await session.scalars(
                select(CompanyRelationship.id).where(
                    (CompanyRelationship.source_company_id == company.id)
                    | (CompanyRelationship.target_company_id == company.id)
                )
            )
        ).all()
    )
    return PromotionResult(True, company.id, len(relationship_ids), "Promoted")


async def reject_candidate(session: AsyncSession, candidate_id: int) -> DiscoveredCompany | None:
    row = await session.get(DiscoveredCompany, candidate_id)
    if row is None:
        return None
    if row.promoted_company_id is not None:
        return row
    row.status = "REJECTED"
    row.verification_status = "REJECTED"
    row.updated_at = datetime.now(timezone.utc)
    evidence = dict(row.verification_evidence_json or {})
    evidence["decision"] = "rejected"
    row.verification_evidence_json = evidence
    await session.commit()
    return row


async def _aliases(session: AsyncSession, company: Company, row: DiscoveredCompany) -> None:
    wanted = [
        (company.name, "LEGAL_NAME"),
        (row.name, "COMMON_NAME"),
        (row.canonical_name or "", "LEGAL_NAME"),
        (row.ticker or "", "TICKER"),
    ]
    existing = {
        alias.alias.casefold()
        for alias in (
            await session.scalars(select(CompanyAlias).where(CompanyAlias.company_id == company.id))
        ).all()
    }
    for value, alias_type in wanted:
        cleaned = (value or "").strip()
        if not cleaned or cleaned.casefold() in existing or cleaned.casefold() == company.name.casefold():
            continue
        session.add(
            CompanyAlias(
                company_id=company.id,
                alias=cleaned,
                alias_type=alias_type,
                source="SUPPLY_CHAIN",
                is_active=True,
            )
        )
        existing.add(cleaned.casefold())


def _pipeline_status(sec_cik: str | None, depth: int) -> str:
    if depth > settings.discovery_max_depth:
        return "BLOCKED"
    if (sec_cik or "").strip():
        return "READY"
    return "NEW"


def _reason(row: DiscoveredCompany) -> str:
    text = f"Promoted from supply-chain candidate {row.name}. {row.discovery_reason}"
    return text[:500]
