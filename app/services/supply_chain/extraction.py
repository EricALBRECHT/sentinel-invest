"""Read one document and store only relationships a rule can explain."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.company import Company
from app.models.company_sync_status import CompanySyncStatus
from app.models.intelligence import CompanyAlias, DocumentCompany, ExternalDocument, ExternalSource
from app.models.supply_chain import DiscoveredCompany
from app.services.intelligence.company_matching import AMBIGUOUS_TOKENS
from app.services.supply_chain.discovery import upsert_candidate
from app.services.supply_chain.relationships import SUPPLY_CHAIN_SOURCE, upsert_relationship
from app.services.supply_chain.rules import CATALOG, Entity, RelationHit, contains_phrase, find_relationships


async def extract_relationships(session: AsyncSession, document_id: int) -> dict:
    document = await session.get(ExternalDocument, document_id)
    if document is None:
        return {"document_id": document_id, "relationships": 0, "candidates": 0, "status": "missing"}
    source = await session.get(ExternalSource, document.source_id)
    links = list(
        (
            await session.scalars(select(DocumentCompany).where(DocumentCompany.document_id == document_id))
        ).all()
    )
    text = "\n".join(part for part in (document.title, document.summary, document.content_text) if part)
    entities = await _entities(session)
    hits = find_relationships(text, entities)
    anchors = {link.company_id for link in links}
    hits = [_orient_partner(hit, anchors) for hit in hits]
    trust = source.trust_level if source is not None else "MEDIUM"
    method = "SEC" if document.document_type == "SEC_FILING" else "RULE"
    seen_at = document.published_at or document.fetched_at
    relationships = 0
    candidates = 0
    seen_candidates: set[str] = set()
    for hit in hits:
        if hit.source.company_id is not None and hit.target.company_id is not None:
            if hit.source.company_id == hit.target.company_id:
                continue
            stored = await upsert_relationship(
                session,
                source_company_id=hit.source.company_id,
                target_company_id=hit.target.company_id,
                relationship_type=hit.relationship_type,
                confidence=hit.confidence,
                importance=hit.importance,
                evidence_text=f"{hit.rule_name}: {hit.excerpt}",
                document_id=document.id,
                discovery_method=method,
                seen_at=seen_at,
                trust_level=trust,
            )
            if stored is not None:
                relationships += 1
            continue
        known = hit.source if hit.source.company_id is not None else hit.target
        unknown = hit.target if hit.source.company_id is not None else hit.source
        if known.company_id is None or unknown.company_id is not None:
            continue
        if _matches_known_company(unknown.label, entities):
            continue
        await upsert_candidate(
            session,
            name=unknown.label,
            ticker=unknown.ticker,
            discovered_from_company_id=known.company_id,
            discovery_reason=(
                f"{hit.relationship_type} via {hit.rule_name} with {known.label}"
            ),
            confidence=hit.confidence,
            document_id=document.id,
        )
        if unknown.label.casefold() not in seen_candidates:
            seen_candidates.add(unknown.label.casefold())
            candidates += 1
    await session.commit()
    return {
        "document_id": document_id,
        "relationships": relationships,
        "candidates": candidates,
        "status": "success",
    }


async def process_company_documents(session: AsyncSession, company_id: int) -> dict:
    company = await session.get(Company, company_id)
    if company is None:
        return {
            "status": "missing",
            "company_id": company_id,
            "documents": 0,
            "relationships": 0,
            "candidates": 0,
        }
    moment = datetime.now(timezone.utc)
    cutoff = moment - timedelta(days=max(1, settings.supply_chain_lookback_days))
    document_ids = list(
        (
            await session.scalars(
                select(ExternalDocument.id)
                .join(DocumentCompany, DocumentCompany.document_id == ExternalDocument.id)
                .where(
                    DocumentCompany.company_id == company_id,
                    (ExternalDocument.published_at.is_(None)) | (ExternalDocument.published_at >= cutoff),
                )
                .order_by(ExternalDocument.published_at.desc(), ExternalDocument.id.desc())
                .limit(100)
            )
        ).all()
    )
    relationships = 0
    candidates = 0
    for document_id in document_ids:
        result = await extract_relationships(session, int(document_id))
        relationships += int(result["relationships"])
        candidates += int(result["candidates"])
    payload = {
        "status": "success",
        "company_id": company_id,
        "documents": len(document_ids),
        "relationships": relationships,
        "candidates": candidates,
    }
    await _mark_success(session, company_id, payload, moment)
    await session.commit()
    return payload


async def select_due_company_ids(session: AsyncSession, *, now: datetime | None = None) -> list[int]:
    moment = now or datetime.now(timezone.utc)
    companies = list(
        (
            await session.scalars(
                select(Company)
                .join(DocumentCompany, DocumentCompany.company_id == Company.id)
                .where(
                    Company.is_active.is_(True),
                    Company.universe_status.in_(("PORTFOLIO", "DEEP_ANALYSIS", "WATCHED")),
                )
                .distinct()
            )
        ).all()
    )
    statuses = {
        row.company_id: row
        for row in (
            await session.scalars(
                select(CompanySyncStatus).where(CompanySyncStatus.source == SUPPLY_CHAIN_SOURCE)
            )
        ).all()
    }
    due: list[Company] = []
    for company in companies:
        hours = _interval_hours(company.universe_status)
        if hours is None:
            continue
        status = statuses.get(company.id)
        if status is None or status.last_success_at is None or _older_than(status.last_success_at, moment, hours):
            due.append(company)
    due.sort(key=lambda company: (_rank(company.universe_status), -int(company.universe_priority or 0), company.id))
    return [company.id for company in due]


def _interval_hours(universe_status: str) -> int | None:
    if universe_status == "PORTFOLIO":
        return settings.supply_chain_portfolio_interval_hours
    if universe_status == "DEEP_ANALYSIS":
        return settings.supply_chain_deep_interval_hours
    if universe_status == "WATCHED":
        return settings.supply_chain_watched_interval_hours
    return None


async def _entities(session: AsyncSession) -> list[Entity]:
    companies = list((await session.scalars(select(Company).where(Company.is_active.is_(True)))).all())
    aliases = list(
        (await session.scalars(select(CompanyAlias).where(CompanyAlias.is_active.is_(True)))).all()
    )
    by_company: dict[int, list[str]] = {}
    for alias in aliases:
        if alias.alias and alias.alias.casefold() not in AMBIGUOUS_TOKENS:
            by_company.setdefault(alias.company_id, []).append(alias.alias)
    entities: list[Entity] = []
    covered: set[str] = set()
    for company in companies:
        names = [company.name]
        if company.ticker:
            names.append(company.ticker)
        names.extend(by_company.get(company.id, []))
        unique = tuple(_unique(names))
        entities.append(Entity(company.name, unique, company.id, company.ticker))
        covered.update(name.casefold() for name in unique)
    for entry in CATALOG:
        if any(name.casefold() in covered for name in entry.names):
            continue
        matched = _catalog_company(entry, companies, by_company)
        if matched is not None:
            entities.append(Entity(entry.label, entry.names, matched.id, matched.ticker))
            continue
        entities.append(Entity(entry.label, entry.names, None, entry.ticker))
    return entities


def _orient_partner(hit: RelationHit, anchors: set[int]) -> RelationHit:
    if hit.relationship_type != "PARTNER" or not anchors:
        return hit
    source_known = hit.source.company_id in anchors
    target_known = hit.target.company_id in anchors
    if source_known or not target_known:
        return hit
    return RelationHit(
        source=hit.target,
        target=hit.source,
        relationship_type=hit.relationship_type,
        confidence=hit.confidence,
        importance=hit.importance,
        rule_name=hit.rule_name,
        excerpt=hit.excerpt,
    )


def _catalog_company(entry, companies: list[Company], alias_names: dict[int, list[str]]) -> Company | None:
    from app.services.discovery_verification.matching import names_match

    found: list[Company] = []
    for company in companies:
        labels = [company.name, *(alias_names.get(company.id) or [])]
        same_name = any(names_match(label, other) for label in entry.names for other in labels)
        same_ticker = bool(entry.ticker and company.ticker and entry.ticker.casefold() == company.ticker.casefold())
        if same_name or same_ticker:
            found.append(company)
    if len(found) == 1:
        return found[0]
    return None


def _matches_known_company(label: str, entities: list[Entity]) -> bool:
    from app.services.discovery_verification.matching import names_match

    return any(
        entity.company_id is not None and (names_match(label, entity.label) or any(names_match(label, name) for name in entity.names))
        for entity in entities
    )


def _unique(names: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for name in names:
        key = name.casefold()
        if not name or key in seen or key in AMBIGUOUS_TOKENS:
            continue
        if not any(contains_phrase(name, name) for _unused in (0,)):
            continue
        seen.add(key)
        ordered.append(name)
    return ordered


async def _mark_success(session: AsyncSession, company_id: int, payload: dict, moment: datetime) -> None:
    row = await session.scalar(
        select(CompanySyncStatus).where(
            CompanySyncStatus.company_id == company_id,
            CompanySyncStatus.source == SUPPLY_CHAIN_SOURCE,
        )
    )
    if row is None:
        row = CompanySyncStatus(
            company_id=company_id,
            source=SUPPLY_CHAIN_SOURCE,
            consecutive_failures=0,
        )
        session.add(row)
    row.last_attempt_at = moment
    row.last_success_at = moment
    row.last_error_at = None
    row.last_error_message = None
    row.last_result_json = payload
    row.consecutive_failures = 0


def _older_than(moment: datetime, now: datetime, hours: int) -> bool:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment < now - timedelta(hours=hours)


def _rank(status: str) -> int:
    if status == "PORTFOLIO":
        return 0
    if status == "DEEP_ANALYSIS":
        return 1
    if status == "WATCHED":
        return 2
    return 3


async def supply_chain_counts(session: AsyncSession) -> dict:
    from app.models.supply_chain import CompanyRelationship

    total = int(
        await session.scalar(select(func_count(CompanyRelationship.id)))
    )
    confirmed = int(
        await session.scalar(
            select(func_count(CompanyRelationship.id)).where(CompanyRelationship.status == "CONFIRMED")
        )
    )
    discovered = int(
        await session.scalar(
            select(func_count(CompanyRelationship.id)).where(CompanyRelationship.status == "DISCOVERED")
        )
    )
    candidates = int(
        await session.scalar(
            select(func_count(DiscoveredCompany.id)).where(DiscoveredCompany.status != "REJECTED")
        )
    )
    last_relationship = await session.scalar(select(CompanyRelationship.updated_at).order_by(CompanyRelationship.updated_at.desc()).limit(1))
    last_sync = await session.scalar(
        select(CompanySyncStatus.last_success_at)
        .where(CompanySyncStatus.source == SUPPLY_CHAIN_SOURCE)
        .order_by(CompanySyncStatus.last_success_at.desc())
        .limit(1)
    )
    last = _as_utc(last_sync) or _as_utc(last_relationship)
    if last_sync is not None and last_relationship is not None:
        last = max(_as_utc(last_sync), _as_utc(last_relationship))
    return {
        "relationships_total": total,
        "confirmed_relationships": confirmed,
        "candidate_relationships": discovered,
        "discovered_companies": candidates,
        "last_processing": last,
    }


def func_count(column):
    from sqlalchemy import func

    return func.count(column)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)
