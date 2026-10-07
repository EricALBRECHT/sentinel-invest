"""Backfill 3 NVIDIA articles to full body, then run force AI analysis sequentially."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone

from rq.exceptions import NoSuchJobError
from rq.job import Job
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.jobs.queues import enqueue_ai_document_analysis, find_active_ai_document_job, redis_connection
from app.models.ai_document_analysis import AiDocumentAnalysis
from app.models.company_score import CompanyScore
from app.models.intelligence import ExternalDocument
from app.services.intelligence.ingestion import backfill_document_article_body

DOC_IDS = [2, 3, 7]
TIMEOUT_SECONDS = 1200


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
        if status in {"failed", "scheduled"}:
            from app.services.jobs.errors import brief_error

            return {"status": status, "error": brief_error(job.exc_info), "result": {}}
        time.sleep(5)
    return {"status": "timeout", "error": "timeout", "result": {}}


async def main() -> None:
    before_scores = {}
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(CompanyScore))).scalars().all()
        before_scores = {row.company_id: row.quality_score for row in rows}

    lengths = []
    for document_id in DOC_IDS:
        async with AsyncSessionLocal() as session:
            before_doc = await session.get(ExternalDocument, document_id)
            before_chars = len(before_doc.content_text or "") if before_doc else 0
            result = await backfill_document_article_body(session, document_id)
            after_doc = await session.get(ExternalDocument, document_id)
            after_chars = len(after_doc.content_text or "") if after_doc else 0
            lengths.append(
                {
                    "document_id": document_id,
                    "title": after_doc.title if after_doc else None,
                    "url": after_doc.url if after_doc else None,
                    "chars_before": before_chars,
                    "chars_after": after_chars,
                    "backfill": result,
                }
            )
            print(
                f"backfill document_id={document_id} before={before_chars} after={after_chars} "
                f"updated={result.get('updated')} reason={result.get('reason')}",
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
            row = await session.scalar(
                select(AiDocumentAnalysis)
                .where(AiDocumentAnalysis.document_id == document_id)
                .order_by(AiDocumentAnalysis.id.desc())
            )
            doc = await session.get(ExternalDocument, document_id)
            runtime = row.runtime_json or {} if row else {}
            result = row.result_json or {} if row else {}
            analyses.append(
                {
                    "document_id": document_id,
                    "title": doc.title if doc else None,
                    "chars": len(doc.content_text or "") if doc else 0,
                    "rq_status": waited.get("status"),
                    "rq_error": waited.get("error"),
                    "status": None if row is None else row.status,
                    "prompt_version": None if row is None else row.prompt_version,
                    "analysis_confidence": None if row is None else row.analysis_confidence,
                    "gpu_duration_ms": None if row is None else row.duration_ms,
                    "input_tokens": runtime.get("tokens_input"),
                    "output_tokens": runtime.get("tokens_output"),
                    "companies": result.get("companies") or [],
                    "events": result.get("events") or [],
                    "relationships": result.get("relationships") or [],
                    "strategic_signals": result.get("strategic_signals") or [],
                    "risks": result.get("risks") or [],
                    "summary": None if row is None else row.summary,
                    "error_message": None if row is None else row.error_message,
                    "raw_output": None if row is None else row.raw_output,
                }
            )
            print(
                f"done document_id={document_id} status={analyses[-1]['status']} "
                f"conf={analyses[-1]['analysis_confidence']} co={len(analyses[-1]['companies'])} "
                f"ev={len(analyses[-1]['events'])} rel={len(analyses[-1]['relationships'])}",
                flush=True,
            )

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(CompanyScore))).scalars().all()
        after_scores = {row.company_id: row.quality_score for row in rows}

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lengths": lengths,
        "analyses": analyses,
        "score_changed": before_scores != after_scores,
    }
    path = "/tmp/ai_quality_v1_1_sample.json"
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
    print(f"wrote {path} score_changed={payload['score_changed']}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
