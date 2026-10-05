"""Company graph limited to depth 1 or 2."""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.supply_chain import CompanyRelationship
from app.services.supply_chain.discovery import discover_candidates


async def get_company_graph(session: AsyncSession, company_id: int, depth: int = 1) -> dict:
    limited = 1 if depth <= 1 else 2
    collected: list[CompanyRelationship] = []
    seen: set[int] = set()
    visited = {company_id}
    frontier = {company_id}
    for _level in range(limited):
        if not frontier:
            break
        rows = list(
            (
                await session.scalars(
                    select(CompanyRelationship).where(
                        or_(
                            CompanyRelationship.source_company_id.in_(frontier),
                            CompanyRelationship.target_company_id.in_(frontier),
                        ),
                        CompanyRelationship.status != "REJECTED",
                    )
                )
            ).all()
        )
        next_frontier: set[int] = set()
        for row in rows:
            if row.id in seen:
                continue
            seen.add(row.id)
            collected.append(row)
            for node_id in (row.source_company_id, row.target_company_id):
                if node_id not in visited:
                    next_frontier.add(node_id)
        visited |= next_frontier
        frontier = next_frontier
    companies = list(
        (await session.scalars(select(Company).where(Company.id.in_(visited)))).all()
    )
    candidates = await discover_candidates(session, company_id)
    return {
        "company_id": company_id,
        "depth": limited,
        "nodes": [
            {"company_id": company.id, "name": company.name, "ticker": company.ticker}
            for company in sorted(companies, key=lambda company: company.id)
        ],
        "edges": [_edge(row) for row in collected],
        "candidates": [_candidate(row) for row in candidates],
    }


def _edge(row: CompanyRelationship) -> dict:
    return {
        "id": row.id,
        "source_company_id": row.source_company_id,
        "target_company_id": row.target_company_id,
        "relationship_type": row.relationship_type,
        "direction": row.direction,
        "confidence": row.confidence,
        "importance": row.importance,
        "first_seen_at": row.first_seen_at,
        "last_seen_at": row.last_seen_at,
        "evidence_count": row.evidence_count,
        "status": row.status,
        "discovery_method": row.discovery_method,
    }


def _candidate(row) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "ticker": row.ticker,
        "country": row.country,
        "website": row.website,
        "discovered_from_company_id": row.discovered_from_company_id,
        "discovery_reason": row.discovery_reason,
        "evidence_count": row.evidence_count,
        "confidence": row.confidence,
        "status": row.status,
        "canonical_name": row.canonical_name,
        "isin": row.isin,
        "exchange": row.exchange,
        "sec_cik": row.sec_cik,
        "entity_type": row.entity_type,
        "verification_status": row.verification_status,
        "verification_confidence": row.verification_confidence,
        "verified_at": row.verified_at,
        "verification_evidence_json": row.verification_evidence_json or {},
        "promoted_company_id": row.promoted_company_id,
    }
