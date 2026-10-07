"""Enqueue a pickled GPU job that reloads llama.cpp with a new n_ctx.

Uses only APIs already present on the GPU worker (unload + _load_llama_locked).
"""

from __future__ import annotations

import os
import time

from redis import Redis
from rq import Queue
from rq.job import Job


def reload_gpu_context(n_ctx: int = 4096) -> dict:
    import os as _os

    from app.jobs.gpu import model_runtime as mr

    _os.environ["AI_MAX_CONTEXT"] = str(int(n_ctx))
    vram_before = mr.query_vram_mb()
    err = None
    ok = True
    with mr._LOCK:
        mr._RUNTIME["llm"] = None
        mr._RUNTIME["backend"] = None
        mr._RUNTIME["context_size"] = None
        mr._RUNTIME["model_memory_mb"] = None
        mr._RUNTIME["warmed"] = False
        try:
            mr._load_llama_locked()
        except Exception as exc:  # noqa: BLE001
            ok = False
            err = f"{type(exc).__name__}: {exc}"
    status = mr.runtime_status()
    vram_after = mr.query_vram_mb()
    return {
        "ok": ok,
        "error": err,
        "requested_n_ctx": int(n_ctx),
        "context_size": status.get("context_size"),
        "backend": status.get("model_backend"),
        "gpu_layers": status.get("gpu_layers"),
        "model_memory_mb": status.get("model_memory_mb"),
        "vram_before_mb": vram_before.get("used"),
        "vram_after_load_mb": vram_after.get("used"),
        "vram_free_mb": vram_after.get("free"),
        "vram_total_mb": vram_after.get("total"),
    }


def main() -> None:
    n_ctx = int(os.environ.get("TARGET_N_CTX", "4096"))
    host = os.environ.get("REDIS_HOST", "127.0.0.1")
    port = int(os.environ.get("REDIS_PORT", "6379"))
    db = int(os.environ.get("REDIS_DB", "0"))
    conn = Redis(host=host, port=port, db=db)
    queue = Queue("gpu", connection=conn)
    job_id = f"gpu-reload-ctx-{n_ctx}"
    existing = None
    try:
        existing = Job.fetch(job_id, connection=conn)
        existing.delete()
    except Exception:  # noqa: BLE001
        pass
    job = queue.enqueue(
        reload_gpu_context,
        n_ctx,
        job_timeout=600,
        result_ttl=3600,
        failure_ttl=3600,
        job_id=job_id,
    )
    print(f"enqueued job_id={job.id} status={job.get_status()}", flush=True)
    for _ in range(120):
        job.refresh()
        status = job.get_status()
        if status == "finished":
            print("RESULT", job.result, flush=True)
            return
        if status == "failed":
            print("FAILED", job.exc_info, flush=True)
            raise SystemExit(1)
        time.sleep(2)
    print("timeout", job.get_status(), flush=True)
    raise SystemExit(2)


if __name__ == "__main__":
    main()
