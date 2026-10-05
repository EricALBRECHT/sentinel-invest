"""Run one discovery expansion. Scores are left to the existing jobs."""

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.company import Company
from app.services.discovery_expansion.policy import assign_lineage, expansion_block_reason
from app.services.discovery_expansion.sources import linked_source_ids, register_sec_source, sec_financial_eligible
from app.services.discovery_expansion.symbols import reliable_market_symbol
from app.services.intelligence.ingestion import sync_company_news
from app.services.supply_chain.extraction import process_company_documents


_MISSING = object()


async def expand_discovered_company(
    session: AsyncSession,
    company_id: int,
    *,
    provider=None,
    enqueue_sec=_MISSING,
    enqueue_market=_MISSING,
) -> dict:
    company = await session.get(Company, company_id)
    if company is None:
        return _result(company_id, "missing", None)
    cycle = await assign_lineage(session, company)
    reason = cycle or expansion_block_reason(company)
    if reason is not None:
        company.discovery_pipeline_status = "BLOCKED"
        await session.commit()
        return _result(company.id, "blocked", company, detail=reason)

    company.discovery_pipeline_status = "COLLECTING"
    await register_sec_source(session, company)
    symbol = reliable_market_symbol(company.ticker, company.exchange)
    if symbol and not (company.market_symbol or "").strip():
        company.market_symbol = symbol
    await session.commit()
    await session.refresh(company)

    collected = await sync_company_news(session, company.id, provider=provider, force=True)
    relationships = await process_company_documents(session, company.id)
    await session.refresh(company)
    moment = datetime.now(timezone.utc)
    company.last_relationship_processing_at = moment
    if collected.get("status") == "error":
        company.discovery_pipeline_status = "COLLECTING"
        await session.commit()
        return _result(
            company.id,
            "collecting",
            company,
            documents=int(collected.get("created") or 0),
            relationships=int(relationships.get("relationships") or 0),
            candidates=int(relationships.get("candidates") or 0),
        )
    company.last_discovery_collection_at = moment
    company.discovery_pipeline_status = "ANALYZED"
    await session.commit()
    enqueue_sec, enqueue_market = _resolved_enqueuers(enqueue_sec, enqueue_market)
    sec_enqueued = _enqueue(enqueue_sec, company.id) if sec_financial_eligible(company) else False
    market_enqueued = _enqueue(enqueue_market, company.id) if (company.market_symbol or "").strip() else False
    source_count = len(await linked_source_ids(session, company.id))
    return _result(
        company.id,
        "analyzed",
        company,
        documents=int(collected.get("created") or 0),
        relationships=int(relationships.get("relationships") or 0),
        candidates=int(relationships.get("candidates") or 0),
        sources=source_count,
        sec_sync_enqueued=sec_enqueued,
        market_sync_enqueued=market_enqueued,
    )


def default_enqueuers():
    from app.jobs.queues import enqueue_market_sync, enqueue_sec_sync

    return enqueue_sec_sync, enqueue_market_sync


def _resolved_enqueuers(enqueue_sec, enqueue_market):
    if enqueue_sec is _MISSING or enqueue_market is _MISSING:
        default_sec, default_market = default_enqueuers()
        if enqueue_sec is _MISSING:
            enqueue_sec = default_sec
        if enqueue_market is _MISSING:
            enqueue_market = default_market
    return enqueue_sec, enqueue_market


def _enqueue(function, company_id: int) -> bool:
    if function is None:
        return False
    try:
        queued = function(company_id)
    except Exception:
        return False
    if isinstance(queued, dict):
        return bool(queued.get("enqueued"))
    return bool(queued)


def _result(
    company_id: int,
    status: str,
    company: Company | None,
    *,
    detail: str | None = None,
    documents: int = 0,
    relationships: int = 0,
    candidates: int = 0,
    sources: int = 0,
    sec_sync_enqueued: bool = False,
    market_sync_enqueued: bool = False,
) -> dict:
    return {
        "company_id": company_id,
        "status": status,
        "detail": detail,
        "discovery_pipeline_status": None if company is None else company.discovery_pipeline_status,
        "discovery_depth": None if company is None else company.discovery_depth,
        "discovered_parent_company_id": None if company is None else company.discovered_parent_company_id,
        "market_symbol": None if company is None else company.market_symbol,
        "universe_status": None if company is None else company.universe_status,
        "documents": documents,
        "relationships": relationships,
        "candidates": candidates,
        "sources": sources,
        "sec_sync_enqueued": sec_sync_enqueued,
        "market_sync_enqueued": market_sync_enqueued,
        "max_depth": settings.discovery_max_depth,
    }
