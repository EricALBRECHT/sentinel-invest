"""Read-only supervision helpers for Actualités & IA dashboard panels.

Does not enqueue jobs or touch financial scores.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.ai_document_analysis import AiDocumentAnalysis
from app.models.intelligence import ExternalDocument, ExternalSource
from app.services.ai.eligibility import assess_ai_eligibility
from app.services.ai.prompts import PROMPT_VERSION
from app.services.ai.service import effective_model_name

# Documents shorter than this (or still marked as RSS teaser) raise a supervision hint.
SHORT_TEXT_CHARS = 500
RECENT_DOC_LIMIT = 15


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


async def supervision_sources(session: AsyncSession) -> list[dict[str, Any]]:
    """One row per external source with 24h document stats."""
    since = _utcnow() - timedelta(hours=24)
    char_len = func.length(func.coalesce(ExternalDocument.content_text, ""))
    stats = (
        await session.execute(
            select(
                ExternalDocument.source_id,
                func.count(ExternalDocument.id),
                func.avg(char_len),
                func.max(ExternalDocument.id),
            )
            .where(ExternalDocument.fetched_at >= since)
            .group_by(ExternalDocument.source_id)
        )
    ).all()
    by_source = {
        int(source_id): {
            "docs_24h": int(count or 0),
            "avg_chars": float(avg_chars) if avg_chars is not None else None,
            "last_document_id": int(last_id) if last_id is not None else None,
        }
        for source_id, count, avg_chars, last_id in stats
    }
    latest_ids = [row["last_document_id"] for row in by_source.values() if row["last_document_id"]]
    titles: dict[int, str | None] = {}
    if latest_ids:
        for doc_id, title in (
            await session.execute(
                select(ExternalDocument.id, ExternalDocument.title).where(ExternalDocument.id.in_(latest_ids))
            )
        ).all():
            titles[int(doc_id)] = title

    sources = (
        await session.scalars(select(ExternalSource).order_by(ExternalSource.is_active.desc(), ExternalSource.name))
    ).all()
    now = _utcnow()
    rows: list[dict[str, Any]] = []
    for source in sources:
        info = by_source.get(source.id, {"docs_24h": 0, "avg_chars": None, "last_document_id": None})
        last_doc_id = info["last_document_id"]
        fetch_at = _aware(source.last_poll_at or source.last_success_at)
        stale = False
        if fetch_at is None:
            stale = bool(source.is_active)
        else:
            age = now - fetch_at
            stale = age > _stale_threshold(source)
        has_error = source.last_error_at is not None and (
            source.last_success_at is None
            or _aware(source.last_error_at) >= (_aware(source.last_success_at) or source.last_error_at)
        )
        avg_chars = info["avg_chars"]
        short_docs = avg_chars is not None and avg_chars < SHORT_TEXT_CHARS and info["docs_24h"] > 0
        if not source.is_active:
            status = "inactive"
        elif has_error:
            status = "error"
        elif stale:
            status = "stale"
        else:
            status = "ok"
        rows.append(
            {
                "id": source.id,
                "name": source.name,
                "source_type": source.source_type,
                "is_active": source.is_active,
                "last_poll_at": source.last_poll_at,
                "last_success_at": source.last_success_at,
                "last_error_at": source.last_error_at,
                "last_error_message": source.last_error_message,
                "docs_24h": info["docs_24h"],
                "avg_chars": None if avg_chars is None else int(round(avg_chars)),
                "last_document_id": last_doc_id,
                "last_document_title": titles.get(last_doc_id) if last_doc_id else None,
                "status": status,
                "stale": stale,
                "has_error": has_error,
                "short_docs": short_docs,
            }
        )
    return rows


async def supervision_recent_documents(session: AsyncSession, *, limit: int = RECENT_DOC_LIMIT) -> list[dict[str, Any]]:
    """Latest intelligence documents with latest AI analysis row when present."""
    docs = (
        await session.execute(
            select(ExternalDocument, ExternalSource)
            .join(ExternalSource, ExternalSource.id == ExternalDocument.source_id)
            .order_by(ExternalDocument.fetched_at.desc(), ExternalDocument.id.desc())
            .limit(limit)
        )
    ).all()
    if not docs:
        return []
    doc_ids = [document.id for document, _ in docs]
    model_name = effective_model_name()
    analyses: dict[int, AiDocumentAnalysis] = {}
    for row in (
        await session.scalars(
            select(AiDocumentAnalysis)
            .where(AiDocumentAnalysis.document_id.in_(doc_ids))
            .order_by(AiDocumentAnalysis.id.desc())
        )
    ).all():
        analyses.setdefault(row.document_id, row)

    rows: list[dict[str, Any]] = []
    for document, source in docs:
        text = document.content_text or ""
        meta = document.metadata_json or {}
        content_source = meta.get("content_source")
        analysis = analyses.get(document.id)
        status = analysis.status if analysis is not None else "UNANALYZED"
        truncated = "[…]" in text or "[...]" in text or content_source == "rss_item"
        short = len(text) < SHORT_TEXT_CHARS
        rows.append(
            {
                "id": document.id,
                "title": document.title,
                "published_at": document.published_at,
                "fetched_at": document.fetched_at,
                "source_name": source.name,
                "char_count": len(text),
                "content_source": content_source,
                "ai_status": status,
                "ai_confidence": None if analysis is None else analysis.analysis_confidence,
                "analysis_id": None if analysis is None else analysis.id,
                "short": short,
                "truncated_hint": truncated,
                "preferred_model_match": (
                    analysis is not None
                    and analysis.model_name == model_name
                    and analysis.prompt_version == PROMPT_VERSION
                ),
            }
        )
    return rows


def _stale_threshold(source: ExternalSource) -> timedelta:
    """Missed-poll threshold aligned to the source interval, with a configurable floor.

    Previously ``age > interval*2 OR age > 48h`` reduced to ``age > min(...)``, so a
    2h poll source looked stale after only 4h. Daily-ish collection needs the floor.
    """
    interval = max(1, int(source.poll_interval_minutes or 720))
    multiplier = max(1, int(settings.intelligence_stale_poll_multiplier or 2))
    floor_hours = max(0, int(settings.intelligence_stale_min_hours or 0))
    threshold = timedelta(minutes=interval * multiplier)
    if floor_hours:
        threshold = max(threshold, timedelta(hours=floor_hours))
    return threshold


async def supervision_ai_gpu(session: AsyncSession) -> dict[str, Any]:
    """GPU heartbeat presence + AI analysis aggregates for the last 24 hours."""
    from app.jobs.queues import QUEUE_GPU, redis_connection
    from app.services.gpu.workers import gpu_workers_for_page
    from rq import Queue
    from rq.registry import StartedJobRegistry

    since = _utcnow() - timedelta(hours=24)
    workers = gpu_workers_for_page()
    primary = None
    for row in workers:
        if row.get("name") == "sentinel-gpu-01":
            primary = row
            break
    if primary is None and workers:
        primary = workers[0]
    online = bool(primary and primary.get("online"))

    gpu_queued = None
    gpu_started = None
    current_job = None
    try:
        connection = redis_connection()
        queue = Queue(QUEUE_GPU, connection=connection)
        gpu_queued = int(queue.count)
        registry = StartedJobRegistry(QUEUE_GPU, connection=connection)
        gpu_started = int(registry.count)
        started_ids = list(registry.get_job_ids())[:1]
        if started_ids:
            from rq.job import Job
            from rq.exceptions import NoSuchJobError

            try:
                job = Job.fetch(started_ids[0], connection=connection)
                meta = job.meta or {}
                current_job = {
                    "job_id": job.id,
                    "document_id": meta.get("document_id"),
                    "status": job.get_status(refresh=True),
                }
            except NoSuchJobError:
                current_job = {"job_id": started_ids[0], "document_id": None, "status": "started"}
    except Exception:
        gpu_queued = None
        gpu_started = None
        current_job = None

    # Latest analysis row per document (avoids counting superseded INVALID/FAILED retries).
    latest_ids = select(func.max(AiDocumentAnalysis.id)).group_by(AiDocumentAnalysis.document_id)
    status_col = AiDocumentAnalysis.status
    latest_in_window = (
        AiDocumentAnalysis.id.in_(latest_ids),
        AiDocumentAnalysis.completed_at.is_not(None),
        AiDocumentAnalysis.completed_at >= since,
    )
    counts = (
        await session.execute(
            select(
                func.coalesce(func.sum(case((status_col == "SUCCESS", 1), else_=0)), 0),
                func.coalesce(func.sum(case((status_col == "INVALID_OUTPUT", 1), else_=0)), 0),
                func.coalesce(func.sum(case((status_col == "FAILED", 1), else_=0)), 0),
            ).where(*latest_in_window)
        )
    ).one()
    success_24h = int(counts[0] or 0)
    invalid_24h = int(counts[1] or 0)
    failed_24h = int(counts[2] or 0)
    avg_duration = await session.scalar(
        select(func.avg(AiDocumentAnalysis.duration_ms)).where(
            status_col == "SUCCESS",
            AiDocumentAnalysis.duration_ms.is_not(None),
            *latest_in_window,
        )
    )
    avg_duration = float(avg_duration) if avg_duration is not None else None

    # Tokens live in runtime_json — average a bounded recent SUCCESS sample.
    token_rows = (
        await session.scalars(
            select(AiDocumentAnalysis)
            .where(
                AiDocumentAnalysis.status == "SUCCESS",
                AiDocumentAnalysis.runtime_json.is_not(None),
                *latest_in_window,
            )
            .order_by(AiDocumentAnalysis.completed_at.desc())
            .limit(50)
        )
    ).all()
    inputs: list[int] = []
    outputs: list[int] = []
    for row in token_rows:
        runtime = row.runtime_json or {}
        tin = runtime.get("tokens_input")
        tout = runtime.get("tokens_output")
        if isinstance(tin, int):
            inputs.append(tin)
        if isinstance(tout, int):
            outputs.append(tout)

    return {
        "worker_name": None if primary is None else primary.get("name"),
        "gpu_online": online,
        "gpu_queued": gpu_queued,
        "gpu_started": gpu_started,
        "current_job": current_job,
        "success_24h": success_24h,
        "invalid_24h": invalid_24h,
        "failed_24h": failed_24h,
        "avg_duration_ms": avg_duration,
        "avg_tokens_input": None if not inputs else int(round(sum(inputs) / len(inputs))),
        "avg_tokens_output": None if not outputs else int(round(sum(outputs) / len(outputs))),
        "workers": workers,
    }


async def supervision_alerts(
    session: AsyncSession,
    *,
    sources: list[dict[str, Any]] | None = None,
    documents: list[dict[str, Any]] | None = None,
    ai_gpu: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Compact anomaly list for the alerts panel."""
    sources = sources if sources is not None else await supervision_sources(session)
    documents = documents if documents is not None else await supervision_recent_documents(session)
    ai_gpu = ai_gpu if ai_gpu is not None else await supervision_ai_gpu(session)
    alerts: list[dict[str, str]] = []

    for source in sources:
        if source["has_error"]:
            message = source.get("last_error_message") or "erreur de fetch"
            alerts.append(
                {
                    "tone": "bad",
                    "code": "source_error",
                    "text": f"Source « {source['name']} » en erreur : {message}",
                }
            )
        elif source["is_active"] and source["stale"]:
            alerts.append(
                {
                    "tone": "warn",
                    "code": "source_stale",
                    "text": f"Source « {source['name']} » sans fetch récent",
                }
            )
        if source["short_docs"]:
            alerts.append(
                {
                    "tone": "warn",
                    "code": "source_short",
                    "text": f"Source « {source['name']} » : textes 24 h anormalement courts",
                }
            )

    short_docs = [doc for doc in documents if doc["short"] or doc["truncated_hint"]]
    if short_docs:
        sample = ", ".join(str(doc["id"]) for doc in short_docs[:3])
        alerts.append(
            {
                "tone": "warn",
                "code": "doc_short",
                "text": f"Document(s) court(s) ou tronqué(s) : {sample}",
            }
        )

    if not ai_gpu.get("gpu_online"):
        name = ai_gpu.get("worker_name") or "sentinel-gpu-01"
        alerts.append({"tone": "bad", "code": "gpu_offline", "text": f"Worker GPU {name} hors ligne"})

    if int(ai_gpu.get("invalid_24h") or 0) > 0:
        alerts.append(
            {
                "tone": "warn",
                "code": "invalid_output",
                "text": f"{ai_gpu['invalid_24h']} analyse(s) INVALID_OUTPUT sur 24 h",
            }
        )
    if int(ai_gpu.get("failed_24h") or 0) > 0:
        alerts.append(
            {
                "tone": "bad",
                "code": "failed",
                "text": f"{ai_gpu['failed_24h']} analyse(s) FAILED sur 24 h",
            }
        )
    return alerts


async def load_document_detail(session: AsyncSession, document_id: int) -> dict[str, Any] | None:
    """Full document + latest AI analysis for the HTML detail page."""
    row = (
        await session.execute(
            select(ExternalDocument, ExternalSource)
            .join(ExternalSource, ExternalSource.id == ExternalDocument.source_id)
            .where(ExternalDocument.id == document_id)
        )
    ).first()
    if row is None:
        return None
    document, source = row
    analysis = await session.scalar(
        select(AiDocumentAnalysis)
        .where(AiDocumentAnalysis.document_id == document_id)
        .order_by(AiDocumentAnalysis.id.desc())
        .limit(1)
    )
    text = document.content_text or ""
    meta = document.metadata_json or {}
    result = None if analysis is None else (analysis.result_json or {})
    runtime = None if analysis is None else (analysis.runtime_json or {})
    eligibility = assess_ai_eligibility(text, title=document.title, metadata=meta)
    return {
        "document": document,
        "source": source,
        "char_count": len(text),
        "content_source": meta.get("content_source"),
        "rss_teaser_chars": meta.get("rss_teaser_chars"),
        "article_chars": meta.get("article_chars"),
        "ai_eligible": eligibility["eligible"],
        "ai_eligible_reason": eligibility["reason"],
        "analysis": analysis,
        "result": result or {},
        "runtime": runtime or {},
        "companies": list((result or {}).get("companies") or []),
        "events": list((result or {}).get("events") or []),
        "relationships": list((result or {}).get("relationships") or []),
        "strategic_signals": list((result or {}).get("strategic_signals") or []),
        "risks": list((result or {}).get("risks") or []),
    }
