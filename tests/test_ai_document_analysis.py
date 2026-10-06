"""AI document analysis stays offline and does not mutate scores."""

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.jobs.ai_document import run_ai_document_analysis
from app.jobs.queues import ai_document_job_id, enqueue_ai_document_analysis, redis_connection
from app.models.ai_document_analysis import AiDocumentAnalysis
from app.models.company import Company
from app.models.company_score import CompanyScore
from app.models.intelligence import DocumentCompany, ExternalDocument, ExternalSource
from app.services.ai.prompts import PROMPT_VERSION
from app.services.ai.schemas import AiDocumentInput, AiDocumentResult
from app.services.ai.service import build_document_payload, effective_model_name
from app.services.ai.validation import validate_ai_result
from tests.test_quality_score import _headers

_VALID = {
    "summary": "Le document décrit un partenariat datacenter.",
    "companies": [
        {
            "name": "NVIDIA Corporation",
            "ticker": "NVDA",
            "role": "SUBJECT",
            "confidence": 90,
            "evidence": "NVIDIA est citée comme fournisseur de GPU.",
        }
    ],
    "events": [
        {
            "type": "PARTNERSHIP",
            "importance": "HIGH",
            "confidence": 88,
            "description": "Partenariat cloud annoncé.",
            "evidence": "partnership datacenter",
        }
    ],
    "relationships": [
        {
            "source_company": "CoreWeave",
            "target_company": "NVIDIA Corporation",
            "type": "CUSTOMER",
            "confidence": 80,
            "evidence": "CoreWeave utilise les GPU NVIDIA",
        }
    ],
    "strategic_signals": [
        {
            "signal": "Demande IA cloud",
            "importance": "HIGH",
            "confidence": 75,
            "evidence": "datacenter expansion",
        }
    ],
    "risks": [
        {
            "risk": "Concentration fournisseur",
            "importance": "MEDIUM",
            "confidence": 70,
            "evidence": "single GPU vendor",
        }
    ],
    "analysis_confidence": 82,
}


@pytest.fixture
def job_redis(monkeypatch):
    monkeypatch.setattr(settings, "redis_db", 15)
    connection = redis_connection()
    connection.flushdb()
    yield connection
    connection.flushdb()
    connection.close()


@pytest.fixture(autouse=True)
def ai_stub(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "stub")
    monkeypatch.setenv("AI_PROVIDER", "stub")
    monkeypatch.setenv("SENTINEL_AI_STUB", "1")


@pytest.fixture(autouse=True)
def patch_job_session_for_sqlite(session_factory, monkeypatch):
    @asynccontextmanager
    async def _job_session():
        async with session_factory() as session:
            yield session

    monkeypatch.setattr("app.jobs.runner.job_session", _job_session)
    monkeypatch.setattr("app.jobs.ai_document.job_session", _job_session)


def test_input_and_output_schemas():
    payload = AiDocumentInput(
        document_id=1,
        title="Test",
        published_at="2026-01-01T00:00:00+00:00",
        source_name="RSS",
        source_type="RSS",
        trust_level="MEDIUM",
        company_context=[],
        content_text="NVIDIA announced a partnership datacenter expansion.",
    )
    assert payload.document_id == 1
    parsed = AiDocumentResult.model_validate(_VALID)
    assert parsed.analysis_confidence == 82


def test_validation_rejects_bad_confidence_and_missing_evidence():
    bad_conf = dict(_VALID)
    bad_conf["analysis_confidence"] = 120
    with pytest.raises(ValueError):
        validate_ai_result(json.dumps(bad_conf))
    no_evidence = dict(_VALID)
    no_evidence["relationships"] = [
        {
            "source_company": "A",
            "target_company": "B",
            "type": "PARTNER",
            "confidence": 50,
            "evidence": "",
        }
    ]
    with pytest.raises(ValueError):
        validate_ai_result(json.dumps(no_evidence))


async def _seed_document(session_factory) -> tuple[int, int]:
    async with session_factory() as session:
        company = Company(name="NVIDIA Corporation", ticker="NVDA", is_active=True)
        session.add(company)
        await session.flush()
        source = ExternalSource(
            name="Test RSS",
            source_type="RSS",
            base_url="https://example.test/rss",
            provider="memory",
            metadata_json={},
        )
        session.add(source)
        await session.flush()
        document = ExternalDocument(
            source_id=source.id,
            title="NVDA partnership datacenter",
            published_at=datetime.now(timezone.utc),
            fetched_at=datetime.now(timezone.utc),
            content_text="NVIDIA announced a partnership datacenter expansion with a cloud provider.",
            document_type="NEWS_ARTICLE",
            metadata_json={"retention": "text_only_v1"},
        )
        session.add(document)
        await session.flush()
        session.add(
            DocumentCompany(
                document_id=document.id,
                company_id=company.id,
                relation_type="SUBJECT",
                match_method="TICKER",
                confidence=95,
            )
        )
        session.add(
            CompanyScore(
                company_id=company.id,
                score_date=datetime.now(timezone.utc).date(),
                quality_score=80,
                overall_confidence_score=70,
                metrics_json={},
                confidence_json={},
                method_version="quality_v1",
            )
        )
        await session.commit()
        return company.id, document.id


def test_end_to_end_stub_analysis_and_score_unchanged(session_factory):
    import asyncio

    company_id, document_id = asyncio.run(_seed_document(session_factory))
    async def _scores():
        async with session_factory() as session:
            before_row = await session.scalar(select(CompanyScore).where(CompanyScore.company_id == company_id))
            return before_row
    before_row = asyncio.run(_scores())
    result = run_ai_document_analysis(document_id)
    assert result["status"] == "SUCCESS"

    async def _after():
        async with session_factory() as session:
            row = await session.scalar(
                select(AiDocumentAnalysis).where(AiDocumentAnalysis.document_id == document_id)
            )
            after_row = await session.scalar(select(CompanyScore).where(CompanyScore.company_id == company_id))
            payload = await build_document_payload(session, document_id)
            return row, after_row, payload

    row, after_row, payload = asyncio.run(_after())
    assert row is not None
    assert row.status == "SUCCESS"
    assert row.prompt_version == PROMPT_VERSION
    assert row.summary
    assert row.result_json
    assert before_row.quality_score == after_row.quality_score
    assert payload.content_text


def test_existing_success_is_skipped(session_factory):
    import asyncio

    _, document_id = asyncio.run(_seed_document(session_factory))
    first = run_ai_document_analysis(document_id)
    second = run_ai_document_analysis(document_id)
    assert first["status"] == "SUCCESS"
    assert second.get("skipped") is True


def test_duplicate_job_enqueue(job_redis):
    first = enqueue_ai_document_analysis(42)
    second = enqueue_ai_document_analysis(42)
    assert first["enqueued"] is True
    assert second["enqueued"] is False
    assert first["job_id"] == ai_document_job_id(42)


def test_invalid_output_persisted(session_factory, monkeypatch):
    import asyncio

    _, document_id = asyncio.run(_seed_document(session_factory))

    def bad_inference(_payload):
        return {"ok": True, "raw_output": "{not-json", "worker_name": "stub"}

    monkeypatch.setattr("app.jobs.ai_document.run_inference", bad_inference)
    result = run_ai_document_analysis(document_id)
    assert result["status"] == "INVALID_OUTPUT"


def test_failed_job(session_factory, monkeypatch):
    import asyncio

    _, document_id = asyncio.run(_seed_document(session_factory))

    def fail_inference(_payload):
        return {"ok": False, "error": "boom", "worker_name": "stub"}

    monkeypatch.setattr("app.jobs.ai_document.run_inference", fail_inference)
    result = run_ai_document_analysis(document_id)
    assert result["status"] == "FAILED"


async def test_api_routes_jwt_and_admin_status(client, session_factory, job_redis):
    import asyncio

    _, document_id = await _seed_document(session_factory)
    assert (await client.post(f"/admin/jobs/ai-document/{document_id}")).status_code == 401
    assert (await client.get(f"/intelligence/documents/{document_id}/ai-analysis")).status_code == 401
    assert (await client.get("/admin/ai/status")).status_code == 401
    headers = await _headers(client, email="ai-doc@example.com")
    queued = await client.post(f"/admin/jobs/ai-document/{document_id}", headers=headers)
    assert queued.status_code in {200, 202}
    status = await client.get("/admin/ai/status", headers=headers)
    assert status.status_code == 200
    body = status.json()
    assert body["prompt_version"] == PROMPT_VERSION
    assert body["model_name"] == effective_model_name()


def test_runtime_backend_metadata_and_cpu_fallback(monkeypatch):
    from app.jobs.gpu import model_runtime as runtime

    monkeypatch.setenv("AI_PROVIDER", "local")
    monkeypatch.setenv("SENTINEL_AI_STUB", "0")
    monkeypatch.setenv("AI_GPU_LAYERS", "20")
    monkeypatch.setattr(runtime, "env_model_path", lambda: "/tmp/missing-model.gguf")
    with pytest.raises(FileNotFoundError):
        runtime.generate_structured_output({"document_id": 1, "content_text": "x", "company_context": []})

    calls = []

    class FakeLlama:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            layers = kwargs.get("n_gpu_layers", 0)
            if layers > 0:
                raise RuntimeError("CUDA OOM")

        def create_chat_completion(self, **_kwargs):
            return {"choices": [{"message": {"content": '{"ok": true}'}}], "usage": {}}

    monkeypatch.setattr(runtime, "env_model_path", lambda: "/tmp/fake.gguf")
    monkeypatch.setattr(runtime.os.path, "isfile", lambda path: True)
    monkeypatch.setattr(runtime, "detect_cuda_build", lambda: {"cuda_available": True})
    monkeypatch.setattr(runtime, "query_vram_mb", lambda: {"total": 3072, "used": 500, "free": 2500})

    import types

    fake_mod = types.SimpleNamespace(Llama=FakeLlama)
    monkeypatch.setitem(__import__("sys").modules, "llama_cpp", fake_mod)
    # Reset runtime
    runtime._RUNTIME.update({"llm": None, "backend": None, "warmed": False})
    with runtime._LOCK:
        runtime._load_llama_locked()
    assert runtime._RUNTIME["backend"] == "CPU"
    assert runtime._RUNTIME["gpu_layers"] == 0


def test_invalid_json_one_repair_max(monkeypatch):
    from app.jobs.gpu import model_runtime as runtime

    monkeypatch.setenv("AI_PROVIDER", "local")
    monkeypatch.setenv("SENTINEL_AI_STUB", "0")
    runtime._RUNTIME.update(
        {
            "llm": object(),
            "backend": "CUDA",
            "gpu_layers": 20,
            "context_size": 2048,
            "model_path": "/models/x.gguf",
            "warmed": True,
        }
    )
    responses = [
        ("not json at all", {"prompt_tokens": 10, "completion_tokens": 5}),
        ('{"summary":"ok","companies":[],"events":[],"relationships":[],"strategic_signals":[],"risks":[],"analysis_confidence":50}', {"prompt_tokens": 10, "completion_tokens": 8}),
    ]

    def fake_run(prompt: str):
        return responses.pop(0)

    monkeypatch.setattr(runtime, "_run_llama_cpp", fake_run)
    monkeypatch.setattr(runtime, "env_model_path", lambda: "/models/x.gguf")
    monkeypatch.setattr(runtime.os.path, "isfile", lambda path: True)
    raw, parsed, meta = runtime.generate_structured_output(
        {
            "document_id": 1,
            "title": "t",
            "source_name": "s",
            "source_type": "RSS",
            "trust_level": "LOW",
            "company_context": [],
            "content_text": "hello",
        }
    )
    assert meta["repair_attempted"] is True
    assert parsed["analysis_confidence"] == 50
    assert "summary" in parsed


def test_persistence_includes_runtime_metadata(session_factory):
    import asyncio

    from app.services.ai.service import ensure_analysis_row, finalize_from_gpu_result

    async def _go():
        _, document_id = await _seed_document(session_factory)
        async with session_factory() as session:
            row = await ensure_analysis_row(session, document_id)
            await session.commit()
            row_id = row.id
        async with session_factory() as session:
            row = await session.get(AiDocumentAnalysis, row_id)
            finalized = await finalize_from_gpu_result(
                session,
                row,
                {
                    "ok": True,
                    "raw_output": json.dumps(
                        {
                            "summary": "Résumé réel.",
                            "companies": [],
                            "events": [],
                            "relationships": [],
                            "strategic_signals": [],
                            "risks": [],
                            "analysis_confidence": 70,
                        }
                    ),
                    "worker_name": "sentinel-gpu-01",
                    "duration_ms": 1234,
                    "model_backend": "CUDA",
                    "gpu_layers": 20,
                    "context_size": 2048,
                    "tokens_input": 100,
                    "tokens_output": 50,
                    "vram_before_mb": 600,
                    "vram_after_mb": 1800,
                },
            )
            await session.commit()
            return finalized.runtime_json, finalized.status

    runtime_json, status = asyncio.run(_go())
    assert status == "SUCCESS"
    assert runtime_json["model_backend"] == "CUDA"
    assert runtime_json["gpu_layers"] == 20
    assert runtime_json["tokens_input"] == 100
