"""Run document 7 only at AI_MAX_CONTEXT=8192 (set via env before launch)."""

from __future__ import annotations

import asyncio
import json
import time

from rq.exceptions import NoSuchJobError
from rq.job import Job
from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.jobs.queues import enqueue_ai_document_analysis, find_active_ai_document_job, redis_connection
from app.models.ai_document_analysis import AiDocumentAnalysis
from app.models.company import Company  # noqa: F401
from app.models.intelligence import ExternalDocument


def wait_job(job_id: str) -> dict:
    connection = redis_connection()
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        try:
            job = Job.fetch(job_id, connection=connection)
        except NoSuchJobError:
            time.sleep(5)
            continue
        status = job.get_status(refresh=True)
        if status == "finished":
            return {"status": status}
        if status in {"failed", "scheduled", "stopped"}:
            return {"status": status, "error": job.exc_info}
        time.sleep(5)
    return {"status": "timeout"}


async def main() -> None:
    print(
        f"doc7_8192 ai_max_context={settings.ai_max_context} output={settings.ai_max_output_tokens}",
        flush=True,
    )
    document_id = 7
    active = await asyncio.to_thread(find_active_ai_document_job, document_id)
    if active is not None:
        await asyncio.to_thread(active.delete)
    queued = await asyncio.to_thread(enqueue_ai_document_analysis, document_id, True)
    print(f"enqueue job_id={queued['job_id']}", flush=True)
    waited = await asyncio.to_thread(wait_job, queued["job_id"])
    print(f"rq_status={waited.get('status')}", flush=True)
    async with AsyncSessionLocal() as session:
        doc = await session.get(ExternalDocument, document_id)
        row = await session.scalar(
            select(AiDocumentAnalysis)
            .where(AiDocumentAnalysis.document_id == document_id)
            .order_by(AiDocumentAnalysis.id.desc())
        )
        runtime = row.runtime_json or {}
        result = row.result_json or {}
        fit = runtime.get("context_fit") or {}
        out = {
            "document_id": document_id,
            "title": doc.title if doc else None,
            "status": row.status,
            "context_size": runtime.get("context_size"),
            "truncated": fit.get("truncated"),
            "pct_document_kept": fit.get("pct_document_kept"),
            "chars_kept": fit.get("chars_kept"),
            "original_chars": fit.get("original_chars"),
            "tokens_input": runtime.get("tokens_input"),
            "tokens_output": runtime.get("tokens_output"),
            "gpu_ms": row.duration_ms,
            "vram_peak_mb": runtime.get("vram_peak_mb"),
            "vram_after_mb": runtime.get("vram_after_mb"),
            "companies": [
                {"name": c.get("name"), "role": c.get("role"), "confidence": c.get("confidence")}
                for c in (result.get("companies") or [])
            ],
            "events": len(result.get("events") or []),
            "relationships": len(result.get("relationships") or []),
            "strategic_signals": len(result.get("strategic_signals") or []),
            "risks": len(result.get("risks") or []),
            "analysis_confidence": row.analysis_confidence,
            "error_message": row.error_message,
            "summary": (row.summary or "")[:240],
            "context_fit": fit,
            "ai_max_context_setting": settings.ai_max_context,
        }
        path = "/tmp/ai_context_ctx8192_doc7.json"
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(out, handle, ensure_ascii=False, indent=2)
        print(json.dumps(out, ensure_ascii=False, indent=2), flush=True)
        print(f"wrote {path}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
