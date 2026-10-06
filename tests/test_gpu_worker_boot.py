"""GPU worker boot must start RQ even when model preload fails."""

import ast
import inspect
import logging
import os
import sys
import threading
import time
from pathlib import Path
from types import ModuleType

import pytest
from rq import SimpleWorker

from app.jobs.gpu import model_runtime, worker

_SCORE_TOKENS = (
    "quality_score",
    "opportunity_score",
    "company_score",
    "financial_metric",
    "app.services.analysis",
    "app.api.routes.scores",
    "app.api.routes.financials",
)


@pytest.fixture
def clean_runtime():
    saved = dict(model_runtime._RUNTIME)
    for key, value in list(model_runtime._RUNTIME.items()):
        model_runtime._RUNTIME[key] = False if isinstance(value, bool) else None
    yield
    model_runtime._RUNTIME.clear()
    model_runtime._RUNTIME.update(saved)


def _silence_heartbeat(monkeypatch):
    monkeypatch.setattr(worker, "redis_from_env", lambda: object())
    monkeypatch.setattr(worker, "_beat", lambda connection, stop: None)


def _fake_worker(monkeypatch, events):
    class FakeWorker:
        def __init__(self, queues, connection=None, name=None):
            events.append(("listen", list(queues), connection, name))

        def work(self, **kwargs):
            events.append(("work", kwargs))

    monkeypatch.setattr(worker, "SimpleWorker", FakeWorker)


def test_preload_success_starts_rq(monkeypatch, caplog):
    events = []
    _silence_heartbeat(monkeypatch)
    _fake_worker(monkeypatch, events)
    monkeypatch.setenv("GPU_QUEUE", "gpu")
    monkeypatch.setenv("GPU_WORKER_NAME", "sentinel-gpu-01")

    def preload():
        events.append("preload")
        return {"ok": True, "backend": "CUDA", "gpu_layers": 20, "status": "loaded"}

    monkeypatch.setattr(model_runtime, "preload_model", preload)
    with caplog.at_level(logging.INFO, logger="sentinel.gpu"):
        worker.main()

    assert events[0] == "preload"
    assert events[1][0] == "listen"
    assert events[1][1] == ["gpu"]
    assert events[1][3] == "sentinel-gpu-01"
    assert events[2][0] == "work"
    assert "gpu_worker_starting" in caplog.text
    assert "gpu_worker_started" in caplog.text
    assert "gpu_worker_mode=simple" in caplog.text
    assert "fork_enabled=false" in caplog.text


def test_preload_exception_still_starts_rq(monkeypatch, caplog):
    events = []
    _silence_heartbeat(monkeypatch)
    _fake_worker(monkeypatch, events)
    monkeypatch.setenv("GPU_QUEUE", "gpu")

    def preload():
        raise RuntimeError("gguf load failed")

    monkeypatch.setattr(model_runtime, "preload_model", preload)
    with caplog.at_level(logging.INFO, logger="sentinel.gpu"):
        worker.main()

    assert events[0][0] == "listen"
    assert events[0][1] == ["gpu"]
    assert events[1][0] == "work"
    assert "ai_preload_failed" in caplog.text
    assert "RuntimeError" in caplog.text
    assert "gpu_worker_started" in caplog.text


def test_boot_does_not_generate_llama_tokens(monkeypatch, tmp_path, clean_runtime):
    events = []
    _silence_heartbeat(monkeypatch)
    _fake_worker(monkeypatch, events)
    monkeypatch.setenv("GPU_QUEUE", "gpu")
    monkeypatch.setenv("AI_PROVIDER", "local")
    monkeypatch.setenv("AI_GPU_LAYERS", "20")
    monkeypatch.delenv("SENTINEL_AI_STUB", raising=False)
    model_file = tmp_path / "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf"
    model_file.write_bytes(b"gguf")
    monkeypatch.setenv("AI_MODEL_PATH", str(model_file))

    chats = []

    class FakeLlama:
        def __init__(self, **kwargs):
            events.append(("ctor", kwargs.get("model_path"), kwargs.get("n_gpu_layers")))

        def create_chat_completion(self, **kwargs):
            chats.append(kwargs)
            return {"choices": [{"message": {"content": "{\"ok\": true}"}}], "usage": {}}

    fake_module = ModuleType("llama_cpp")
    fake_module.Llama = FakeLlama
    monkeypatch.setitem(sys.modules, "llama_cpp", fake_module)
    monkeypatch.setattr(model_runtime, "query_vram_mb", lambda: {"total": 3019, "used": 200, "free": 2819})
    monkeypatch.setattr(
        model_runtime,
        "detect_cuda_build",
        lambda: {"llama_cpp_import": True, "cuda_available": True, "supports_sm_61_hint": "61"},
    )

    worker.main()

    assert chats == []
    assert "create_chat_completion" not in Path("app/jobs/gpu/worker.py").read_text(encoding="utf-8")
    assert "warmup_model" not in Path("app/jobs/gpu/worker.py").read_text(encoding="utf-8")
    assert events[0][0] == "ctor"
    assert events[1][0] == "listen"
    assert events[1][1] == ["gpu"]
    assert events[2][0] == "work"


def test_preload_loads_the_gguf_only_once(monkeypatch, tmp_path, clean_runtime):
    model_file = tmp_path / "model.gguf"
    model_file.write_bytes(b"gguf")
    monkeypatch.setenv("AI_PROVIDER", "local")
    monkeypatch.setenv("AI_GPU_LAYERS", "20")
    monkeypatch.delenv("SENTINEL_AI_STUB", raising=False)
    monkeypatch.setenv("AI_MODEL_PATH", str(model_file))
    constructed = []

    class FakeLlama:
        def __init__(self, **kwargs):
            constructed.append(kwargs["model_path"])

        def create_chat_completion(self, **kwargs):
            raise AssertionError("preload must not generate tokens")

    fake_module = ModuleType("llama_cpp")
    fake_module.Llama = FakeLlama
    monkeypatch.setitem(sys.modules, "llama_cpp", fake_module)
    monkeypatch.setattr(model_runtime, "query_vram_mb", lambda: {"total": None, "used": None, "free": None})
    monkeypatch.setattr(
        model_runtime,
        "detect_cuda_build",
        lambda: {"cuda_available": True},
    )

    first = model_runtime.preload_model()
    second = model_runtime.preload_model()

    assert constructed == [str(model_file)]
    assert first["ok"] is True
    assert first["backend"] == "CUDA"
    assert first["gpu_layers"] == 20
    assert first["status"] == "loaded"
    assert second["backend"] == first["backend"]
    assert model_runtime._RUNTIME["llm"] is not None


def test_worker_listens_to_the_gpu_queue(monkeypatch):
    seen = {}
    _silence_heartbeat(monkeypatch)
    monkeypatch.setenv("GPU_QUEUE", "gpu")
    monkeypatch.setattr(model_runtime, "preload_model", lambda: {"ok": True, "status": "loaded"})

    class FakeWorker:
        def __init__(self, queues, connection=None, name=None):
            seen["queues"] = list(queues)
            seen["connection"] = connection
            seen["name"] = name

        def work(self, **kwargs):
            seen["work"] = kwargs

    monkeypatch.setattr(worker, "SimpleWorker", FakeWorker)
    worker.main()
    assert seen["queues"] == ["gpu"]
    assert seen["connection"] is not None
    assert seen["work"]["with_scheduler"] is False


def test_gpu_boot_does_not_touch_financial_scores():
    for relative in ("app/jobs/gpu/worker.py", "app/jobs/gpu/model_runtime.py", "app/jobs/gpu/ai_document.py"):
        source = Path(relative).read_text(encoding="utf-8")
        tree = ast.parse(source)
        modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
        for module in modules:
            assert not any(token in module for token in _SCORE_TOKENS)
        for token in _SCORE_TOKENS:
            assert token not in source


def test_heartbeat_thread_is_started(monkeypatch):
    started = []
    original = threading.Thread

    def tracking(*args, **kwargs):
        thread = original(*args, **kwargs)
        if kwargs.get("name") == "gpu-heartbeat":
            started.append(thread.name)
        return thread

    monkeypatch.setattr(worker.threading, "Thread", tracking)
    _silence_heartbeat(monkeypatch)
    _fake_worker(monkeypatch, [])
    monkeypatch.setattr(model_runtime, "preload_model", lambda: {"ok": True})
    monkeypatch.setenv("GPU_QUEUE", "gpu")
    worker.main()
    assert started == ["gpu-heartbeat"]


def test_gpu_worker_uses_simple_worker():
    source = Path("app/jobs/gpu/worker.py").read_text(encoding="utf-8")
    assert "from rq import SimpleWorker" in source
    assert "from rq import Worker" not in source
    assert "SimpleWorker(" in source
    tree = ast.parse(source)
    forked = [
        node
        for node in ast.walk(tree)
        if (isinstance(node, ast.Attribute) and node.attr == "fork")
        or (isinstance(node, ast.Name) and node.id == "fork")
    ]
    assert forked == []
    execute = inspect.getsource(SimpleWorker.execute_job)
    assert "os.fork" not in execute
    core = Path("app/jobs/worker.py").read_text(encoding="utf-8")
    assert "SimpleWorker" not in core


def test_gpu_job_execution_does_not_fork(monkeypatch, caplog):
    calls = []

    def forbidden(*_args, **_kwargs):
        calls.append("fork")
        raise AssertionError("os.fork")

    monkeypatch.setattr(os, "fork", forbidden)
    monkeypatch.setenv("SENTINEL_AI_STUB", "1")
    monkeypatch.setattr(
        "app.jobs.gpu.ai_document.query_vram_mb",
        lambda: {"total": None, "used": None, "free": None},
    )
    from app.jobs.gpu.ai_document import analyze_document_payload

    with caplog.at_level(logging.INFO, logger="sentinel.gpu.ai"):
        result = analyze_document_payload(
            {
                "document_id": 9,
                "run_id": "run-simple",
                "content_text": "hello",
                "analyzed_char_count": 5,
            }
        )
    assert result["ok"] is True
    assert calls == []
    assert "run_id=run-simple" in caplog.text
    assert "document_id=9" in caplog.text
    assert "input_chars=5" in caplog.text
    assert "duration_ms=" in caplog.text


def test_two_jobs_reuse_the_same_model(monkeypatch, tmp_path, clean_runtime):
    model_file = tmp_path / "model.gguf"
    model_file.write_bytes(b"gguf")
    monkeypatch.setenv("AI_PROVIDER", "local")
    monkeypatch.setenv("AI_GPU_LAYERS", "20")
    monkeypatch.delenv("SENTINEL_AI_STUB", raising=False)
    monkeypatch.setenv("AI_MODEL_PATH", str(model_file))
    constructed = []

    class FakeLlama:
        def __init__(self, **kwargs):
            constructed.append(id(self))

        def create_chat_completion(self, **kwargs):
            return {
                "choices": [{"message": {"content": "{\"ok\": true}"}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2},
            }

    fake_module = ModuleType("llama_cpp")
    fake_module.Llama = FakeLlama
    monkeypatch.setitem(sys.modules, "llama_cpp", fake_module)
    monkeypatch.setattr(model_runtime, "query_vram_mb", lambda: {"total": 3019, "used": 200, "free": 2819})
    monkeypatch.setattr(model_runtime, "detect_cuda_build", lambda: {"cuda_available": True})
    model_runtime.preload_model()
    loaded = model_runtime._RUNTIME["llm"]
    payload = {
        "document_id": 4,
        "run_id": "same-runtime",
        "title": "Probe",
        "published_at": "",
        "source_name": "probe",
        "source_type": "news",
        "trust_level": "low",
        "company_context": [],
        "content_truncated": False,
        "original_char_count": 4,
        "analyzed_char_count": 4,
        "content_text": "test",
    }
    first = model_runtime.generate_structured_output(payload)
    second = model_runtime.generate_structured_output(payload)
    assert model_runtime._RUNTIME["llm"] is loaded
    assert constructed == [id(loaded)]
    assert first[2]["tokens_input"] == 4
    assert second[2]["tokens_output"] == 2


def test_heartbeat_continues_during_a_job(monkeypatch):
    beats = []
    monkeypatch.setattr(worker, "HEARTBEAT_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(worker, "heartbeat_payload", lambda: {"name": "sentinel-gpu-01"})
    monkeypatch.setattr(worker, "record_heartbeat", lambda connection, payload: beats.append(payload["name"]))
    stop = threading.Event()
    thread = threading.Thread(target=worker._beat, args=(object(), stop), name="gpu-heartbeat", daemon=True)
    thread.start()
    time.sleep(0.18)
    stop.set()
    thread.join(1)
    assert beats.count("sentinel-gpu-01") >= 2


def test_gpu_queue_stays_sequential():
    source = Path("app/jobs/gpu/worker.py").read_text(encoding="utf-8")
    assert "ThreadPool" not in source
    assert worker.GPU_QUEUE_NAMES == ("gpu",)
    assert source.count("SimpleWorker(") == 1
