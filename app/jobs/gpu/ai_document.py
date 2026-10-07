"""RQ entrypoint for GPU document analysis."""

from __future__ import annotations

import logging
import os
import time

from app.jobs.gpu.model_runtime import (
    env_model_name,
    generate_structured_output,
    query_vram_mb,
    runtime_status,
)

logger = logging.getLogger("sentinel.gpu.ai")


def _token_field(value: object) -> object:
    return value if isinstance(value, int) else "unavailable"


def analyze_document_payload(payload: dict) -> dict:
    """Analyze one document payload. No database access."""
    started = time.monotonic()
    worker = os.environ.get("GPU_WORKER_NAME", "sentinel-gpu-01")
    status = runtime_status()
    content = payload.get("content_text") or ""
    input_chars = payload.get("analyzed_char_count")
    if not isinstance(input_chars, int):
        input_chars = len(content)
    logger.info(
        "ai_document_started run_id=%s document_id=%s backend=%s gpu_layers=%s input_chars=%s",
        payload.get("run_id"),
        payload.get("document_id"),
        status.get("model_backend"),
        status.get("gpu_layers"),
        input_chars,
    )
    vram_before = query_vram_mb()
    try:
        requested = payload.get("requested_n_ctx")
        from app.jobs.gpu.model_runtime import env_provider

        if (
            isinstance(requested, int)
            and requested > 0
            and env_provider() != "stub"
            and os.environ.get("SENTINEL_AI_STUB") != "1"
        ):
            try:
                from app.jobs.gpu.model_runtime import ensure_context_size

                ensure_context_size(requested)
            except ImportError:
                logger.warning("ensure_context_size unavailable; using worker AI_MAX_CONTEXT")
        raw, parsed, meta = generate_structured_output(payload)
        vram_during = query_vram_mb()
        vram_after = query_vram_mb()
        status = runtime_status()
        duration_ms = int((time.monotonic() - started) * 1000)
        peak_vals = [
            v
            for v in (
                vram_before.get("used"),
                vram_during.get("used"),
                vram_after.get("used"),
                status.get("vram_after_load_mb"),
            )
            if isinstance(v, int)
        ]
        logger.info(
            "ai_document_finished run_id=%s document_id=%s backend=%s gpu_layers=%s "
            "input_chars=%s input_tokens=%s output_tokens=%s duration_ms=%s "
            "context_size=%s vram_before_mb=%s vram_during_mb=%s vram_after_mb=%s vram_peak_mb=%s",
            payload.get("run_id"),
            payload.get("document_id"),
            status.get("model_backend") or meta.get("backend"),
            status.get("gpu_layers"),
            input_chars,
            _token_field(meta.get("tokens_input")),
            _token_field(meta.get("tokens_output")),
            duration_ms,
            status.get("context_size"),
            vram_before.get("used"),
            vram_during.get("used"),
            vram_after.get("used"),
            max(peak_vals) if peak_vals else None,
        )
        return {
            "ok": True,
            "raw_output": raw,
            "result": parsed,
            "model_name": env_model_name(),
            "model_version": status.get("model_path"),
            "worker_name": worker,
            "runtime_device": status.get("model_backend") or meta.get("backend") or "unknown",
            "model_backend": status.get("model_backend") or meta.get("backend"),
            "gpu_layers": status.get("gpu_layers"),
            "context_size": status.get("context_size"),
            "model_memory_mb": status.get("model_memory_mb"),
            "tokens_input": meta.get("tokens_input"),
            "tokens_output": meta.get("tokens_output"),
            "repair_attempted": meta.get("repair_attempted", False),
            "duration_ms": duration_ms,
            "vram_before_mb": vram_before.get("used"),
            "vram_during_mb": vram_during.get("used"),
            "vram_after_mb": vram_after.get("used"),
            "vram_peak_mb": max(peak_vals) if peak_vals else None,
            "vram_after_load_mb": status.get("vram_after_load_mb"),
            "vram_total_mb": vram_after.get("total") or vram_before.get("total"),
            "vram_free_after_mb": vram_after.get("free"),
            "context_fit": payload.get("context_fit"),
        }
    except Exception as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.warning(
            "ai_document_failed run_id=%s document_id=%s backend=%s gpu_layers=%s "
            "input_chars=%s input_tokens=unavailable output_tokens=unavailable duration_ms=%s error_type=%s",
            payload.get("run_id"),
            payload.get("document_id"),
            runtime_status().get("model_backend"),
            runtime_status().get("gpu_layers"),
            input_chars,
            duration_ms,
            type(exc).__name__,
        )
        return {
            "ok": False,
            "error": str(exc),
            "raw_output": None,
            "worker_name": worker,
            "model_backend": runtime_status().get("model_backend"),
            "duration_ms": duration_ms,
            "vram_before_mb": vram_before.get("used"),
            "vram_after_mb": query_vram_mb().get("used"),
        }
