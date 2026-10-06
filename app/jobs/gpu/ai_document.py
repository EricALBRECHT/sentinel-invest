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


def analyze_document_payload(payload: dict) -> dict:
    """Analyze one document payload. No database access."""
    started = time.monotonic()
    worker = os.environ.get("GPU_WORKER_NAME", "sentinel-gpu-01")
    vram_before = query_vram_mb()
    try:
        raw, parsed, meta = generate_structured_output(payload)
        vram_after = query_vram_mb()
        status = runtime_status()
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
            "duration_ms": int((time.monotonic() - started) * 1000),
            "vram_before_mb": vram_before.get("used"),
            "vram_after_mb": vram_after.get("used"),
            "vram_total_mb": vram_after.get("total") or vram_before.get("total"),
            "vram_free_after_mb": vram_after.get("free"),
        }
    except Exception as exc:
        logger.warning("ai_document_failed error_type=%s", type(exc).__name__)
        return {
            "ok": False,
            "error": str(exc),
            "raw_output": None,
            "worker_name": worker,
            "model_backend": runtime_status().get("model_backend"),
            "duration_ms": int((time.monotonic() - started) * 1000),
            "vram_before_mb": vram_before.get("used"),
            "vram_after_mb": query_vram_mb().get("used"),
        }
