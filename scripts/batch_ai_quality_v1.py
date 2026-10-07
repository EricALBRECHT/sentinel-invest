"""Sequential AI document quality batch (one GPU job at a time). Read-only scoring untouched."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime, timezone

from rq.exceptions import NoSuchJobError
from rq.job import Job
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.jobs.queues import enqueue_ai_document_analysis, find_active_ai_document_job, redis_connection
from app.models.ai_document_analysis import AiDocumentAnalysis
from app.models.company import Company
from app.models.company_score import CompanyScore
from app.models.intelligence import DocumentCompany, ExternalDocument, ExternalSource

DOC_IDS = [1, 2, 3, 4, 5, 6, 7, 13]
POLL_SECONDS = 5
TIMEOUT_SECONDS = 1200


def wait_job(job_id: str, timeout: int = TIMEOUT_SECONDS) -> dict:
    connection = redis_connection()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            job = Job.fetch(job_id, connection=connection)
        except NoSuchJobError:
            time.sleep(POLL_SECONDS)
            continue
        status = job.get_status(refresh=True)
        if status == "finished":
            return {"status": status, "result": job.result if isinstance(job.result, dict) else {}, "error": None}
        if status == "failed":
            from app.services.jobs.errors import brief_error

            return {"status": status, "result": {}, "error": brief_error(job.exc_info)}
        if status == "scheduled":
            from app.services.jobs.errors import brief_error

            return {
                "status": status,
                "result": {},
                "error": brief_error(job.exc_info) or "job entered scheduled state unexpectedly",
            }
        time.sleep(POLL_SECONDS)
    return {"status": "timeout", "result": {}, "error": f"timeout after {timeout}s"}


async def load_scores() -> dict[int, object]:
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(CompanyScore))).scalars().all()
        return {row.company_id: row.quality_score for row in rows}


async def load_row(document_id: int) -> dict:
    async with AsyncSessionLocal() as session:
        document = await session.get(ExternalDocument, document_id)
        source = await session.get(ExternalSource, document.source_id) if document else None
        analysis = await session.scalar(
            select(AiDocumentAnalysis)
            .where(AiDocumentAnalysis.document_id == document_id)
            .order_by(AiDocumentAnalysis.id.desc())
        )
        links = (
            await session.execute(
                select(Company.ticker, Company.name, DocumentCompany.relation_type)
                .join(DocumentCompany, DocumentCompany.company_id == Company.id)
                .where(DocumentCompany.document_id == document_id)
            )
        ).all()
        content = document.content_text or "" if document else ""
        return {
            "title": document.title if document else None,
            "source": source.name if source else None,
            "links": [{"ticker": t, "name": n, "relation_type": r} for t, n, r in links],
            "content_chars": len(content),
            "content_preview": content[:400],
            "analysis": analysis,
        }


def summarize(analysis: AiDocumentAnalysis | None) -> dict:
    if analysis is None:
        return {
            "status": None,
            "gpu_duration_ms": None,
            "input_tokens": None,
            "output_tokens": None,
            "analysis_confidence": None,
            "companies_count": 0,
            "events_count": 0,
            "relationships_count": 0,
            "strategic_signals_count": 0,
            "risks_count": 0,
            "error_message": None,
            "result_json": None,
            "runtime_json": None,
            "run_id": None,
            "summary": None,
            "raw_output": None,
        }
    result = analysis.result_json or {}
    runtime = analysis.runtime_json or {}
    return {
        "status": analysis.status,
        "gpu_duration_ms": analysis.duration_ms or runtime.get("duration_ms"),
        "input_tokens": runtime.get("tokens_input"),
        "output_tokens": runtime.get("tokens_output"),
        "analysis_confidence": analysis.analysis_confidence,
        "companies_count": len(result.get("companies") or []),
        "events_count": len(result.get("events") or []),
        "relationships_count": len(result.get("relationships") or []),
        "strategic_signals_count": len(result.get("strategic_signals") or []),
        "risks_count": len(result.get("risks") or []),
        "error_message": analysis.error_message,
        "result_json": result if analysis.status == "SUCCESS" else None,
        "runtime_json": runtime,
        "run_id": runtime.get("run_id"),
        "summary": analysis.summary,
        "raw_output": analysis.raw_output,
    }


async def main_async() -> int:
    before_scores = await load_scores()
    results = []
    print(f"batch_start docs={DOC_IDS} at={datetime.now(timezone.utc).isoformat()}", flush=True)

    for document_id in DOC_IDS:
        active = await asyncio.to_thread(find_active_ai_document_job, document_id)
        if active is not None:
            print(
                f"orphan document_id={document_id} active={active.id} status={active.get_status()}",
                flush=True,
            )
            await asyncio.to_thread(active.delete)
            print(f"deleted_orphan document_id={document_id}", flush=True)

        meta = await load_row(document_id)
        print(f"enqueue document_id={document_id} title={meta['title']!r}", flush=True)
        queued = await asyncio.to_thread(enqueue_ai_document_analysis, document_id, True)
        print(
            f"enqueued document_id={document_id} job_id={queued['job_id']} status={queued['status']}",
            flush=True,
        )
        waited = await asyncio.to_thread(wait_job, queued["job_id"])
        print(
            f"job_done document_id={document_id} rq_status={waited['status']} error={waited.get('error')}",
            flush=True,
        )
        after = await load_row(document_id)
        summary = summarize(after["analysis"])
        row = {
            "document_id": document_id,
            "title": after["title"],
            "source": after["source"],
            "linked_companies": after["links"],
            "content_chars": after["content_chars"],
            "content_preview": after["content_preview"],
            "rq_status": waited["status"],
            "rq_error": waited.get("error"),
            "rq_result": waited.get("result"),
            **summary,
        }
        results.append(row)
        print(
            f"persisted document_id={document_id} status={row['status']} "
            f"conf={row['analysis_confidence']} gpu_ms={row['gpu_duration_ms']} "
            f"tok_in={row['input_tokens']} tok_out={row['output_tokens']}",
            flush=True,
        )

    after_scores = await load_scores()
    score_changed = before_scores != after_scores
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "document_ids": DOC_IDS,
        "score_changed": score_changed,
        "results": results,
    }
    out = "/tmp/ai_quality_batch_v1.json"
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
    print(f"wrote {out} score_changed={score_changed}", flush=True)
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
