"""Store one directed relationship per source, target, and type."""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.supply_chain import CompanyRelationship, RelationshipEvidence
from app.services.supply_chain.rules import automatic_status, importance_for
from app.services.universe.manager import recalculate_universe_priority

_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
SUPPLY_CHAIN_SOURCE = "supply_chain"


async def upsert_relationship(
    session: AsyncSession,
    *,
    source_company_id: int,
    target_company_id: int,
    relationship_type: str,
    confidence: int,
    importance: str,
    evidence_text: str,
    document_id: int | None = None,
    event_id: int | None = None,
    discovery_method: str = "RULE",
    seen_at: datetime | None = None,
    trust_level: str = "MEDIUM",
) -> CompanyRelationship | None:
    if source_company_id == target_company_id:
        return None
    moment = seen_at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    row = await session.scalar(
        select(CompanyRelationship).where(
            CompanyRelationship.source_company_id == source_company_id,
            CompanyRelationship.target_company_id == target_company_id,
            CompanyRelationship.relationship_type == relationship_type,
        )
    )
    if row is None:
        row = CompanyRelationship(
            source_company_id=source_company_id,
            target_company_id=target_company_id,
            relationship_type=relationship_type,
            direction="SOURCE_TO_TARGET",
            confidence=confidence,
            importance=importance,
            first_seen_at=moment,
            last_seen_at=moment,
            evidence_count=0,
            status="DISCOVERED",
            discovery_method=discovery_method,
            updated_at=moment,
        )
        session.add(row)
        await session.flush()
    added = await add_evidence(
        session,
        relationship_id=row.id,
        document_id=document_id,
        event_id=event_id,
        evidence_text=evidence_text,
        confidence=confidence,
    )
    if added:
        count = int(
            await session.scalar(
                select(func.count())
                .select_from(RelationshipEvidence)
                .where(RelationshipEvidence.relationship_id == row.id)
            )
        )
        row.evidence_count = count
        row.confidence = max(row.confidence, confidence)
        row.importance = _higher(
            row.importance,
            importance_for(importance, evidence_text, count, trust_level),
        )
        row.last_seen_at = max(_aware(row.last_seen_at), moment)
        if row.status not in {"REJECTED", "STALE", "CONFIRMED"}:
            row.status = automatic_status(row.confidence, row.evidence_count)
        await _note_supply_origin(session, row)
    elif moment > _aware(row.last_seen_at):
        row.last_seen_at = moment
    row.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return row


async def add_evidence(
    session: AsyncSession,
    *,
    relationship_id: int,
    evidence_text: str,
    confidence: int,
    document_id: int | None = None,
    event_id: int | None = None,
) -> RelationshipEvidence | None:
    if document_id is not None:
        existing = await session.scalar(
            select(RelationshipEvidence).where(
                RelationshipEvidence.relationship_id == relationship_id,
                RelationshipEvidence.document_id == document_id,
            )
        )
        if existing is not None:
            return None
    evidence = RelationshipEvidence(
        relationship_id=relationship_id,
        document_id=document_id,
        event_id=event_id,
        evidence_text=evidence_text[:4000],
        confidence=confidence,
    )
    session.add(evidence)
    await session.flush()
    return evidence


async def list_relationships(
    session: AsyncSession,
    company_id: int,
    *,
    relationship_type: str | None = None,
    status: str | None = None,
    direction: str | None = None,
    confidence_min: int | None = None,
) -> list[CompanyRelationship]:
    statement = select(CompanyRelationship)
    if direction == "outbound":
        statement = statement.where(CompanyRelationship.source_company_id == company_id)
    elif direction == "inbound":
        statement = statement.where(CompanyRelationship.target_company_id == company_id)
    else:
        statement = statement.where(
            (CompanyRelationship.source_company_id == company_id)
            | (CompanyRelationship.target_company_id == company_id)
        )
    if relationship_type:
        statement = statement.where(CompanyRelationship.relationship_type == relationship_type)
    if status:
        statement = statement.where(CompanyRelationship.status == status)
    if confidence_min is not None:
        statement = statement.where(CompanyRelationship.confidence >= confidence_min)
    statement = statement.order_by(CompanyRelationship.confidence.desc(), CompanyRelationship.id.asc())
    return list((await session.scalars(statement)).all())


async def _note_supply_origin(session: AsyncSession, row: CompanyRelationship) -> None:
    """Several strong proofs may add the existing supply-chain priority. Status stays put."""
    if row.evidence_count < 2 or row.confidence < 75:
        return
    for company_id in (row.source_company_id, row.target_company_id):
        company = await session.get(Company, company_id)
        if company is None or company.discovery_source is not None:
            continue
        company.discovery_source = "SUPPLY_CHAIN"
        company.discovery_reason = "Named by more than one supply-chain document"
        await recalculate_universe_priority(session, company)


def _higher(current: str, proposed: str) -> str:
    if _RANK.get(proposed, 0) >= _RANK.get(current, 0):
        return proposed
    return current


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
