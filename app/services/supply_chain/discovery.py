"""Candidates for distinctive names that are not followed companies yet."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.supply_chain import DiscoveredCompany
from app.services.supply_chain.rules import automatic_status

_CANDIDATE_CONFIRM_AT = 2


async def upsert_candidate(
    session: AsyncSession,
    *,
    name: str,
    discovered_from_company_id: int,
    discovery_reason: str,
    confidence: int,
    document_id: int | None,
    ticker: str | None = None,
) -> DiscoveredCompany:
    row = await session.scalar(select(DiscoveredCompany).where(DiscoveredCompany.name == name))
    moment = datetime.now(timezone.utc)
    reason = discovery_reason[:500]
    if row is None:
        row = DiscoveredCompany(
            name=name,
            ticker=ticker,
            discovered_from_company_id=discovered_from_company_id,
            discovery_reason=reason,
            evidence_count=1 if document_id is not None else 1,
            confidence=confidence,
            status="CANDIDATE",
            document_ids=[document_id] if document_id is not None else [],
            entity_type="UNKNOWN",
            verification_status="UNVERIFIED",
            verification_confidence=0,
            verification_evidence_json={},
            updated_at=moment,
        )
        session.add(row)
        await session.flush()
        return row
    seen = list(row.document_ids or [])
    if document_id is not None and document_id in seen:
        if confidence > row.confidence:
            row.confidence = confidence
        row.discovery_reason = _merge_reason(row.discovery_reason, reason)
        row.updated_at = moment
        await session.flush()
        return row
    if document_id is not None:
        seen.append(document_id)
        row.document_ids = list(seen)
        row.evidence_count = len(seen)
    row.confidence = max(row.confidence, confidence)
    if confidence >= row.confidence:
        row.discovery_reason = _merge_reason(row.discovery_reason, reason)
    if row.status == "CANDIDATE" and automatic_status(row.confidence, row.evidence_count) == "CONFIRMED":
        if row.evidence_count >= _CANDIDATE_CONFIRM_AT and row.confidence >= 90:
            row.status = "VERIFIED"
    row.updated_at = moment
    await session.flush()
    return row


async def discover_candidates(
    session: AsyncSession,
    company_id: int | None = None,
    *,
    status: str | None = None,
    confidence_min: int | None = None,
    verification_status: str | None = None,
) -> list[DiscoveredCompany]:
    statement = select(DiscoveredCompany)
    if company_id is not None:
        statement = statement.where(DiscoveredCompany.discovered_from_company_id == company_id)
    if status:
        statement = statement.where(DiscoveredCompany.status == status)
    if confidence_min is not None:
        statement = statement.where(DiscoveredCompany.confidence >= confidence_min)
    if verification_status:
        statement = statement.where(DiscoveredCompany.verification_status == verification_status)
    statement = statement.order_by(DiscoveredCompany.confidence.desc(), DiscoveredCompany.id.asc())
    return list((await session.scalars(statement)).all())


def _merge_reason(current: str, extra: str) -> str:
    if extra in current:
        return current[:500]
    merged = f"{current}; {extra}"
    return merged[:500]
