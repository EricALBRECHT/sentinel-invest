"""Run AI Document V1.1 context experiment on docs 2/3/7 and write a comparison report."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone

from rq.exceptions import NoSuchJobError
from rq.job import Job
from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.jobs.queues import enqueue_ai_document_analysis, find_active_ai_document_job, redis_connection
from app.models.ai_document_analysis import AiDocumentAnalysis
from app.models.company import Company  # noqa: F401
from app.models.company_score import CompanyScore
from app.models.intelligence import ExternalDocument

DOC_IDS = [2, 3, 7]
TIMEOUT_SECONDS = 1800


def wait_job(job_id: str) -> dict:
    connection = redis_connection()
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            job = Job.fetch(job_id, connection=connection)
        except NoSuchJobError:
            time.sleep(5)
            continue
        status = job.get_status(refresh=True)
        if status == "finished":
            return {"status": status, "result": job.result if isinstance(job.result, dict) else {}}
        if status in {"failed", "scheduled", "stopped"}:
            from app.services.jobs.errors import brief_error

            return {"status": status, "error": brief_error(job.exc_info), "result": {}}
        time.sleep(5)
    return {"status": "timeout", "error": "timeout", "result": {}}


def summarize_row(row: AiDocumentAnalysis | None, doc: ExternalDocument | None) -> dict:
    if row is None:
        return {"status": None}
    result = row.result_json or {}
    runtime = row.runtime_json or {}
    fit = runtime.get("context_fit") or {}
    companies = result.get("companies") or []
    return {
        "document_id": row.document_id,
        "title": None if doc is None else doc.title,
        "stored_chars": 0 if doc is None else len(doc.content_text or ""),
        "status": row.status,
        "prompt_version": row.prompt_version,
        "analysis_confidence": row.analysis_confidence,
        "error_message": row.error_message,
        "companies": [
            {
                "name": c.get("name"),
                "role": c.get("role"),
                "confidence": c.get("confidence"),
            }
            for c in companies
        ],
        "roles": [c.get("role") for c in companies],
        "events": len(result.get("events") or []),
        "relationships": len(result.get("relationships") or []),
        "strategic_signals": len(result.get("strategic_signals") or []),
        "risks": len(result.get("risks") or []),
        "gpu_ms": row.duration_ms,
        "tokens_input": runtime.get("tokens_input"),
        "tokens_output": runtime.get("tokens_output"),
        "context_size": runtime.get("context_size"),
        "vram_before_mb": runtime.get("vram_before_mb"),
        "vram_during_mb": runtime.get("vram_during_mb"),
        "vram_after_mb": runtime.get("vram_after_mb"),
        "vram_peak_mb": runtime.get("vram_peak_mb"),
        "vram_after_load_mb": runtime.get("vram_after_load_mb"),
        "vram_total_mb": runtime.get("vram_total_mb"),
        "context_fit": fit,
        "truncated": fit.get("truncated"),
        "document_tokens_est": fit.get("original_document_tokens_est"),
        "tokens_kept_est": fit.get("tokens_kept_after_fit_est"),
        "chars_kept": fit.get("chars_kept"),
        "pct_document_kept": fit.get("pct_document_kept"),
        "summary": (row.summary or "")[:200],
    }


async def snapshot_docs(label: str) -> dict:
    rows = {}
    async with AsyncSessionLocal() as session:
        for document_id in DOC_IDS:
            doc = await session.get(ExternalDocument, document_id)
            analysis = await session.scalar(
                select(AiDocumentAnalysis)
                .where(AiDocumentAnalysis.document_id == document_id)
                .order_by(AiDocumentAnalysis.id.desc())
            )
            rows[str(document_id)] = summarize_row(analysis, doc)
    return {"label": label, "ai_max_context_setting": settings.ai_max_context, "docs": rows}


async def main() -> None:
    label = f"ctx{settings.ai_max_context}"
    before_scores = {}
    async with AsyncSessionLocal() as session:
        score_rows = (await session.execute(select(CompanyScore))).scalars().all()
        before_scores = {row.company_id: row.quality_score for row in score_rows}

    baseline = await snapshot_docs("pre_run_latest")
    print(
        f"experiment start label={label} ai_max_context={settings.ai_max_context} "
        f"output={settings.ai_max_output_tokens}",
        flush=True,
    )

    analyses = []
    for document_id in DOC_IDS:
        active = await asyncio.to_thread(find_active_ai_document_job, document_id)
        if active is not None:
            await asyncio.to_thread(active.delete)
        queued = await asyncio.to_thread(enqueue_ai_document_analysis, document_id, True)
        print(f"enqueue document_id={document_id} job_id={queued['job_id']}", flush=True)
        waited = await asyncio.to_thread(wait_job, queued["job_id"])
        async with AsyncSessionLocal() as session:
            doc = await session.get(ExternalDocument, document_id)
            row = await session.scalar(
                select(AiDocumentAnalysis)
                .where(AiDocumentAnalysis.document_id == document_id)
                .order_by(AiDocumentAnalysis.id.desc())
            )
            item = summarize_row(row, doc)
            item["rq_status"] = waited.get("status")
            item["rq_error"] = waited.get("error")
            analyses.append(item)
            fit = item.get("context_fit") or {}
            print(
                f"done document_id={document_id} status={item['status']} truncated={item.get('truncated')} "
                f"pct_kept={item.get('pct_document_kept')} tok_in={item.get('tokens_input')} "
                f"ctx={item.get('context_size')} peak_vram={item.get('vram_peak_mb')} "
                f"roles={item.get('roles')} err={(item.get('error_message') or '')[:80]}",
                flush=True,
            )
            if fit:
                print(
                    f"  fit original_chars={fit.get('original_chars')} chars_kept={fit.get('chars_kept')} "
                    f"sys_tok={fit.get('system_prompt_tokens_est')} "
                    f"user_before={fit.get('user_prompt_tokens_before_fit_est')} "
                    f"total_before={fit.get('total_tokens_before_fit_est')} "
                    f"total_after={fit.get('tokens_kept_after_fit_est')} "
                    f"budget={fit.get('prompt_token_budget')}",
                    flush=True,
                )

    async with AsyncSessionLocal() as session:
        score_rows = (await session.execute(select(CompanyScore))).scalars().all()
        after_scores = {row.company_id: row.quality_score for row in score_rows}

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "label": label,
        "ai_max_context": settings.ai_max_context,
        "ai_max_output_tokens": settings.ai_max_output_tokens,
        "ai_gpu_layers": settings.ai_gpu_layers,
        "baseline_latest": baseline,
        "analyses": analyses,
        "score_changed": before_scores != after_scores,
    }
    path = f"/tmp/ai_context_{label}.json"
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
    print(f"wrote {path} score_changed={payload['score_changed']}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
