"""One-shot GPU inventory. No model is loaded."""

import os
import subprocess
from datetime import datetime, timezone
from typing import Any

from app.jobs.gpu import WORKER_VERSION
from app.jobs.gpu.registry import save_probe

_EMPTY = {"available": False, "name": None, "memory_total": None, "memory_free": None}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def worker_name() -> str:
    return os.environ.get("GPU_WORKER_NAME", "sentinel-gpu-01")


def query_gpu(device: str = "0") -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "-i",
                device,
                "--query-gpu=name,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return dict(_EMPTY)
    if completed.returncode != 0:
        return dict(_EMPTY)
    line = completed.stdout.strip().splitlines()
    if not line:
        return dict(_EMPTY)
    parts = [part.strip() for part in line[0].split(",")]
    if len(parts) < 3:
        return dict(_EMPTY)
    try:
        total = int(float(parts[-2]))
        free = int(float(parts[-1]))
    except ValueError:
        return dict(_EMPTY)
    return {
        "available": True,
        "name": ",".join(parts[:-2]).strip() or None,
        "memory_total": total,
        "memory_free": free,
    }


def build_probe_payload(device: str | None = None) -> dict[str, Any]:
    from app.jobs.gpu.model_runtime import env_context, env_gpu_layers, env_model_name, env_model_path, runtime_status

    chosen = device if device is not None else os.environ.get("GPU_DEVICE", "0")
    gpu = query_gpu(chosen)
    ai_runtime = runtime_status()
    return {
        "worker": worker_name(),
        "gpu_available": bool(gpu["available"]),
        "gpu_name": gpu["name"],
        "gpu_memory_total_mb": gpu["memory_total"],
        "gpu_memory_free_mb": gpu["memory_free"],
        "cuda_visible": bool(gpu["available"]),
        "ai_provider": os.environ.get("AI_PROVIDER", "local"),
        "model_name": env_model_name(),
        "ai_model_name": env_model_name(),
        "ai_model_path": env_model_path(),
        "model_loaded": bool(ai_runtime.get("model_loaded")),
        "model_backend": ai_runtime.get("model_backend"),
        "gpu_layers": ai_runtime.get("gpu_layers") if ai_runtime.get("gpu_layers") is not None else env_gpu_layers(),
        "context_size": ai_runtime.get("context_size") if ai_runtime.get("context_size") is not None else env_context(),
        "model_memory_mb": ai_runtime.get("model_memory_mb"),
        "ai_runtime_device": ai_runtime.get("model_backend") or ai_runtime.get("device"),
        "timestamp": utc_now(),
    }


def gpu_system_probe() -> dict[str, Any]:
    payload = build_probe_payload()
    connection = _job_connection()
    if connection is not None:
        save_probe(connection, str(payload["worker"]), payload)
    return payload


def _job_connection():
    try:
        from rq import get_current_job
    except ImportError:
        return None
    job = get_current_job()
    if job is None:
        return None
    return job.connection


def heartbeat_payload(gpu: dict[str, Any] | None = None) -> dict[str, Any]:
    from app.jobs.gpu.model_runtime import env_context, env_gpu_layers, env_model_name, runtime_status

    current = gpu if gpu is not None else query_gpu(os.environ.get("GPU_DEVICE", "0"))
    ai = runtime_status()
    return {
        "name": worker_name(),
        "hostname": _hostname(),
        "gpu_name": current["name"],
        "gpu_memory_total": current["memory_total"],
        "status": "online" if current["available"] else "degraded",
        "last_heartbeat": utc_now(),
        "version": WORKER_VERSION,
        "ai_provider": os.environ.get("AI_PROVIDER", "local"),
        "model_name": env_model_name(),
        "model_loaded": bool(ai.get("model_loaded")),
        "model_backend": ai.get("model_backend"),
        "gpu_layers": ai.get("gpu_layers") if ai.get("gpu_layers") is not None else env_gpu_layers(),
        "context_size": ai.get("context_size") if ai.get("context_size") is not None else env_context(),
        "model_memory_mb": ai.get("model_memory_mb"),
    }


def _hostname() -> str:
    import socket

    return socket.gethostname()
