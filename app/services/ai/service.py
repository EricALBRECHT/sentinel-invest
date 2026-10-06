"""Prepare payloads, run inference, validate, and persist AI analyses."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.ai_document_analysis import AiDocumentAnalysis
from app.models.company import Company
from app.models.intelligence import DocumentCompany, ExternalDocument, ExternalSource
from app.services.ai.prompts import PROMPT_VERSION
from app.services.ai.schemas import AiDocumentAnalysisRead, AiDocumentInput, AiDocumentResult, CompanyContextItem
from app.services.ai.validation import validate_ai_result

logger = logging.getLogger("sentinel.ai")


def effective_model_name() -> str:
    return settings.ai_model_name.strip() or "Qwen2.5-1.5B-Instruct-Q4_K_M"


def effective_model_version() -> str:
    return (settings.ai_model_version or "").strip() or None


async def build_document_payload(session: AsyncSession, document_id: int) -> AiDocumentInput:
    document = await session.get(ExternalDocument, document_id)
    if document is None:
        raise LookupError("Document not found")
    source = await session.get(ExternalSource, document.source_id)
    if source is None:
        raise LookupError("Document source not found")
    text = (document.content_text or "").strip()
    if not text:
        raise ValueError("Document has no analyzable text")
    original_len = len(text)
    limit = max(500, settings.ai_max_input_chars)
    truncated = original_len > limit
    if truncated:
        text = _truncate_text(text, limit)
    links = (
        await session.execute(
            select(DocumentCompany, Company)
            .join(Company, Company.id == DocumentCompany.company_id)
            .where(DocumentCompany.document_id == document_id)
        )
    ).all()
    context = [
        CompanyContextItem(
            company_id=company.id,
            name=company.name,
            ticker=company.ticker,
            relation_type=link.relation_type,
        )
        for link, company in links
    ]
    published = document.published_at.isoformat() if document.published_at else None
    return AiDocumentInput(
        document_id=document_id,
        title=document.title,
        published_at=published,
        source_name=source.name,
        source_type=source.source_type,
        trust_level=source.trust_level,
        company_context=context,
        content_text=text,
        content_truncated=truncated,
        original_char_count=original_len,
        analyzed_char_count=len(text),
    )


async def get_latest_analysis(
    session: AsyncSession,
    document_id: int,
    *,
    model_name: str | None = None,
    prompt_version: str | None = None,
) -> AiDocumentAnalysis | None:
    selected_model = model_name or effective_model_name()
    selected_prompt = prompt_version or PROMPT_VERSION
    return await session.scalar(
        select(AiDocumentAnalysis)
        .where(
            AiDocumentAnalysis.document_id == document_id,
            AiDocumentAnalysis.model_name == selected_model,
            AiDocumentAnalysis.prompt_version == selected_prompt,
        )
        .order_by(AiDocumentAnalysis.id.desc())
    )


async def ensure_analysis_row(
    session: AsyncSession,
    document_id: int,
    *,
    force: bool = False,
) -> AiDocumentAnalysis:
    existing = await get_latest_analysis(session, document_id)
    if existing is not None and should_skip_success(existing, force=force):
        return existing
    if existing is not None and existing.status in {"PENDING", "RUNNING"} and not force:
        return existing
    if existing is not None:
        row = existing
        # Reuse the unique (document_id, model_name, prompt_version) row.
        row.status = "PENDING"
        row.error_message = None
        row.result_json = None
        row.runtime_json = None
        row.summary = None
        row.analysis_confidence = None
        row.started_at = None
        row.completed_at = None
        row.duration_ms = None
        row.worker_name = None
        # Keep prior raw_output until a new inference overwrites it.
        row.model_version = effective_model_version()
        await session.flush()
        return row
    row = AiDocumentAnalysis(
        document_id=document_id,
        model_name=effective_model_name(),
        model_version=effective_model_version(),
        prompt_version=PROMPT_VERSION,
        status="PENDING",
    )
    session.add(row)
    await session.flush()
    return row


def should_skip_success(row: AiDocumentAnalysis, *, force: bool = False) -> bool:
    """Skip only reusable SUCCESS rows when force is false."""
    if force or row.status != "SUCCESS":
        return False
    if settings.ai_provider == "local" and is_stub_analysis(row):
        return False
    return True


def is_stub_analysis(row: AiDocumentAnalysis) -> bool:
    worker = (row.worker_name or "").strip().lower()
    if worker in {"stub", "sentinel-stub"}:
        return True
    runtime = row.runtime_json or {}
    backend = str(runtime.get("model_backend") or runtime.get("runtime_device") or "").strip().lower()
    return backend == "stub"


async def mark_running(session: AsyncSession, row: AiDocumentAnalysis) -> None:
    row.status = "RUNNING"
    row.started_at = datetime.now(timezone.utc)
    row.model_version = effective_model_version()
    await session.flush()


async def finalize_from_gpu_result(
    session: AsyncSession,
    row: AiDocumentAnalysis,
    gpu_payload: dict,
    *,
    run_id: str | None = None,
) -> AiDocumentAnalysis:
    finished = datetime.now(timezone.utc)
    row.completed_at = finished
    row.worker_name = gpu_payload.get("worker_name")
    row.duration_ms = gpu_payload.get("duration_ms")
    row.runtime_json = {
        "run_id": run_id or gpu_payload.get("run_id"),
        "model_backend": gpu_payload.get("model_backend") or gpu_payload.get("runtime_device"),
        "gpu_layers": gpu_payload.get("gpu_layers"),
        "context_size": gpu_payload.get("context_size"),
        "model_memory_mb": gpu_payload.get("model_memory_mb"),
        "tokens_input": gpu_payload.get("tokens_input"),
        "tokens_output": gpu_payload.get("tokens_output"),
        "vram_before_mb": gpu_payload.get("vram_before_mb"),
        "vram_after_mb": gpu_payload.get("vram_after_mb"),
        "vram_total_mb": gpu_payload.get("vram_total_mb"),
        "repair_attempted": gpu_payload.get("repair_attempted", False),
    }
    if row.started_at is not None and row.duration_ms is None:
        started = row.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        row.duration_ms = int((finished - started).total_seconds() * 1000)
    if not gpu_payload.get("ok"):
        row.status = "FAILED"
        row.error_message = str(gpu_payload.get("error") or "GPU analysis failed")[:500]
        row.raw_output = gpu_payload.get("raw_output")
        await session.flush()
        return row
    raw = gpu_payload.get("raw_output") or ""
    if not raw and gpu_payload.get("result") is not None:
        import json

        raw = json.dumps(gpu_payload["result"], ensure_ascii=False)
    row.raw_output = raw if isinstance(raw, str) else str(raw)
    try:
        parsed, payload = validate_ai_result(row.raw_output)
    except ValueError as exc:
        row.status = "INVALID_OUTPUT"
        row.error_message = str(exc)[:500]
        await session.flush()
        return row
    row.status = "SUCCESS"
    row.summary = parsed.summary
    row.analysis_confidence = parsed.analysis_confidence
    row.result_json = payload
    row.error_message = None
    await session.flush()
    return row


def serialize_analysis(row: AiDocumentAnalysis) -> AiDocumentAnalysisRead:
    result = None
    if row.result_json:
        try:
            result = AiDocumentResult.model_validate(row.result_json)
        except Exception:
            result = None
    return AiDocumentAnalysisRead(
        id=row.id,
        document_id=row.document_id,
        model_name=row.model_name,
        model_version=row.model_version,
        prompt_version=row.prompt_version,
        status=row.status,
        summary=row.summary,
        result=result,
        analysis_confidence=row.analysis_confidence,
        started_at=row.started_at,
        completed_at=row.completed_at,
        duration_ms=row.duration_ms,
        worker_name=row.worker_name,
        error_message=row.error_message,
        runtime=row.runtime_json,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def admin_ai_status(session: AsyncSession) -> dict:
    from app.services.gpu.workers import list_worker_records

    success = await session.scalar(
        select(func.count()).select_from(AiDocumentAnalysis).where(AiDocumentAnalysis.status == "SUCCESS")
    )
    failed = await session.scalar(
        select(func.count()).select_from(AiDocumentAnalysis).where(AiDocumentAnalysis.status == "FAILED")
    )
    invalid = await session.scalar(
        select(func.count()).select_from(AiDocumentAnalysis).where(AiDocumentAnalysis.status == "INVALID_OUTPUT")
    )
    pending = await session.scalar(
        select(func.count())
        .select_from(AiDocumentAnalysis)
        .where(AiDocumentAnalysis.status.in_(("PENDING", "RUNNING")))
    )
    last_completed = await session.scalar(
        select(func.max(AiDocumentAnalysis.completed_at)).where(AiDocumentAnalysis.status == "SUCCESS")
    )
    avg_duration = await session.scalar(
        select(func.avg(AiDocumentAnalysis.duration_ms)).where(
            AiDocumentAnalysis.status == "SUCCESS",
            AiDocumentAnalysis.duration_ms.is_not(None),
        )
    )
    workers = list_worker_records()
    online = sum(1 for item in workers if item.get("online"))
    loaded = None
    for item in workers:
        probe = item.get("probe") or {}
        if probe.get("model_loaded"):
            loaded = str(probe.get("model_loaded"))
            break
    return {
        "provider": settings.ai_provider,
        "model_name": effective_model_name(),
        "model_version": effective_model_version(),
        "prompt_version": PROMPT_VERSION,
        "gpu_workers_online": online,
        "model_loaded_hint": loaded,
        "success_count": int(success or 0),
        "failed_count": int(failed or 0),
        "invalid_output_count": int(invalid or 0),
        "pending_or_running": int(pending or 0),
        "last_completed_at": last_completed,
        "average_duration_ms": float(avg_duration) if avg_duration is not None else None,
    }


def run_inference(payload: dict) -> dict:
    """Dispatch to stub or GPU queue depending on configuration."""
    if settings.ai_provider == "stub":
        from app.jobs.gpu.ai_document import analyze_document_payload

        return analyze_document_payload(payload)
    from app.jobs.ai_document import run_gpu_and_wait

    started = time.monotonic()
    result = run_gpu_and_wait(payload)
    result.setdefault("duration_ms", int((time.monotonic() - started) * 1000))
    return result


def _truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = int(limit * 0.75)
    tail = limit - head - 40
    if tail < 0:
        return text[:limit]
    return f"{text[:head]}\n\n[... contenu tronqué pour respecter la limite d'analyse ...]\n\n{text[-tail:]}"
