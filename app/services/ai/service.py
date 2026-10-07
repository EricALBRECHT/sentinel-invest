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
from app.services.ai.prompts import PROMPT_VERSION, SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from app.services.ai.schemas import AiDocumentAnalysisRead, AiDocumentInput, AiDocumentResult, CompanyContextItem
from app.services.ai.validation import validate_ai_result

logger = logging.getLogger("sentinel.ai")


def effective_model_name() -> str:
    return settings.ai_model_name.strip() or "Qwen2.5-1.5B-Instruct-Q4_K_M"


def render_user_prompt(payload: dict) -> str:
    """Render the user half of the analysis prompt."""
    import json

    return USER_PROMPT_TEMPLATE.format(
        document_id=payload.get("document_id"),
        title=payload.get("title") or "",
        published_at=payload.get("published_at") or "",
        source_name=payload.get("source_name") or "",
        source_type=payload.get("source_type") or "",
        trust_level=payload.get("trust_level") or "",
        company_context=json.dumps(payload.get("company_context") or [], ensure_ascii=False),
        content_truncated=payload.get("content_truncated", False),
        original_char_count=payload.get("original_char_count", 0),
        analyzed_char_count=payload.get("analyzed_char_count", 0),
        content_text=payload.get("content_text") or "",
    )


def render_analysis_prompt(payload: dict) -> str:
    """Render the core-owned prompt so GPU workers do not need a prompt redeploy."""
    return f"{SYSTEM_PROMPT}\n\n{render_user_prompt(payload)}"


def _estimate_tokens(text: str) -> int:
    """Char→token estimate for Qwen-class models (no tokenizer on core).

    Calibrated from observed GPU failures (~2.1k prompt tokens on long articles).
    """
    return max(1, (len(text) + 4) // 5)


def prompt_token_budget() -> int:
    """Max prompt tokens so prompt + max_output fits in ai_max_context.

    Near-full prompts leave too few tokens for JSON generation and cause INVALID_OUTPUT.
    """
    return max(256, settings.ai_max_context - settings.ai_max_output_tokens - 32)


def _context_fit_metrics(
    *,
    original_content: str,
    kept_content: str,
    before_payload: dict,
    after_payload: dict,
    truncated: bool,
) -> dict:
    """Build the pre-inference context/truncation report requested for ctx experiments."""
    system_tokens = _estimate_tokens(SYSTEM_PROMPT)
    user_before = render_user_prompt(before_payload)
    user_after = render_user_prompt(after_payload)
    user_before_tokens = _estimate_tokens(user_before)
    user_after_tokens = _estimate_tokens(user_after)
    doc_tokens_original = _estimate_tokens(original_content)
    doc_tokens_kept = _estimate_tokens(kept_content)
    total_before = system_tokens + user_before_tokens
    total_after = system_tokens + user_after_tokens
    original_chars = len(original_content)
    kept_chars = len(kept_content)
    pct = round(100.0 * kept_chars / original_chars, 2) if original_chars else 100.0
    return {
        "original_chars": original_chars,
        "original_document_tokens_est": doc_tokens_original,
        "system_prompt_tokens_est": system_tokens,
        "user_prompt_tokens_before_fit_est": user_before_tokens,
        "user_prompt_tokens_after_fit_est": user_after_tokens,
        "total_tokens_before_fit_est": total_before,
        "tokens_kept_after_fit_est": total_after,
        "document_tokens_kept_est": doc_tokens_kept,
        "chars_kept": kept_chars,
        "pct_document_kept": pct,
        "truncated": bool(truncated),
        "context_max": settings.ai_max_context,
        "output_reserved_tokens": settings.ai_max_output_tokens,
        "prompt_token_budget": prompt_token_budget(),
    }


def fit_payload_to_context(payload: dict) -> dict:
    """Shrink content_text until the rendered prompt fits the GPU context budget.

    Does not change GPU n_ctx / RQ — only the text sent to the model.
    Attaches payload['context_fit'] with truncation metrics for experiments.
    """
    fitted = dict(payload)
    content = str(fitted.get("content_text") or "")
    original = int(fitted.get("original_char_count") or len(content))
    budget = prompt_token_budget()
    before_payload = {
        **fitted,
        "content_text": content,
        "analyzed_char_count": len(content),
        "original_char_count": original,
        "content_truncated": bool(fitted.get("content_truncated", False)),
    }

    def tokens_for(text: str) -> int:
        trial = {
            **fitted,
            "content_text": text,
            "analyzed_char_count": len(text),
            "original_char_count": original,
        }
        return _estimate_tokens(render_analysis_prompt(trial))

    truncated = bool(fitted.get("content_truncated", False))
    kept = content
    if tokens_for(content) > budget:
        low, high = 400, len(content)
        best = _truncate_text(content, 400)
        while low <= high:
            mid = (low + high) // 2
            candidate = _truncate_text(content, mid)
            if tokens_for(candidate) <= budget:
                best = candidate
                low = mid + 1
            else:
                high = mid - 1
        kept = best
        truncated = True

    fitted["content_text"] = kept
    fitted["content_truncated"] = truncated
    fitted["original_char_count"] = original
    fitted["analyzed_char_count"] = len(kept)
    after_payload = dict(fitted)
    fit_metrics = _context_fit_metrics(
        original_content=content,
        kept_content=kept,
        before_payload=before_payload,
        after_payload=after_payload,
        truncated=truncated,
    )
    fit_metrics["original_chars"] = original
    fit_metrics["pct_document_kept"] = round(100.0 * len(kept) / original, 2) if original else 100.0
    if original > len(content):
        fit_metrics["original_document_tokens_est"] = _estimate_tokens("x" * original)
    fitted["context_fit"] = fit_metrics
    logger.info(
        "ai_content_fit document_id=%s original_chars=%s chars_kept=%s pct_kept=%s "
        "tokens_before=%s tokens_after=%s truncated=%s context_max=%s output_reserved=%s",
        fitted.get("document_id"),
        fit_metrics["original_chars"],
        fit_metrics["chars_kept"],
        fit_metrics["pct_document_kept"],
        fit_metrics["total_tokens_before_fit_est"],
        fit_metrics["tokens_kept_after_fit_est"],
        fit_metrics["truncated"],
        fit_metrics["context_max"],
        fit_metrics["output_reserved_tokens"],
    )
    return fitted


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
    payload = AiDocumentInput(
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
    fitted = fit_payload_to_context(payload.model_dump())
    return AiDocumentInput.model_validate(fitted)


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
    peak_candidates = [
        gpu_payload.get("vram_peak_mb"),
        gpu_payload.get("vram_before_mb"),
        gpu_payload.get("vram_after_mb"),
        gpu_payload.get("vram_during_mb"),
        gpu_payload.get("vram_after_load_mb"),
    ]
    peak_values = [int(v) for v in peak_candidates if isinstance(v, int)]
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
        "vram_during_mb": gpu_payload.get("vram_during_mb"),
        "vram_peak_mb": max(peak_values) if peak_values else None,
        "vram_total_mb": gpu_payload.get("vram_total_mb"),
        "vram_after_load_mb": gpu_payload.get("vram_after_load_mb"),
        "repair_attempted": gpu_payload.get("repair_attempted", False),
        "context_fit": gpu_payload.get("context_fit"),
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
