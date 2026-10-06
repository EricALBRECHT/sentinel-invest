"""GPU worker V1 stays on Redis. It does not open PostgreSQL."""

import ast
import inspect
import subprocess
import time
from pathlib import Path

import pytest
from rq import Queue
from rq.job import Job

from app.core.config import settings
from app.jobs.gpu import probe as gpu_probe
from app.jobs.gpu.connection import redis_target_from_env
from app.jobs.gpu.health import health_check
from app.jobs.gpu.probe import build_probe_payload, gpu_system_probe, query_gpu
from app.jobs.gpu.registry import (
    HEARTBEAT_TTL_SECONDS,
    get_worker,
    list_workers,
    record_heartbeat,
    save_probe,
    worker_key,
)
from app.jobs.gpu.worker import GPU_QUEUE_NAMES
from app.jobs.queues import (
    QUEUE_GPU,
    QUEUE_NAMES,
    QUEUE_SEC,
    enqueue_gpu_probe,
    gpu_probe_job_id,
    redis_connection,
)
from app.jobs.worker import main as core_worker_main
from app.web.viewmodels import present_gpu_worker
from tests.test_quality_score import _headers

_DEPLOY = Path("deploy/gpu-worker")
_FORBIDDEN = ("sqlalchemy", "asyncpg", "psycopg", "app.db", "app.core", "alembic")


@pytest.fixture
def job_redis(monkeypatch):
    monkeypatch.setattr(settings, "redis_db", 15)
    connection = redis_connection()
    connection.flushdb()
    yield connection
    connection.flushdb()
    connection.close()


def test_gpu_package_does_not_touch_postgres():
    for path in Path("app/jobs/gpu").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
            for module in modules:
                assert not any(module == item or module.startswith(f"{item}.") for item in _FORBIDDEN)


def test_core_worker_does_not_consume_the_gpu_queue():
    source = inspect.getsource(core_worker_main)
    assert "QUEUE_GPU" not in source
    assert '"gpu"' not in source
    assert source.index("QUEUE_ANALYSIS") < source.index("QUEUE_SEC")
    assert GPU_QUEUE_NAMES == ("gpu",)
    assert QUEUE_GPU in QUEUE_NAMES
    assert QUEUE_GPU not in ("default", "sec", "analysis", "market", "intelligence")
    scheduler = Path("app/jobs/scheduler.py").read_text()
    assert "gpu_system_probe" not in scheduler


def test_worker_redis_stays_on_the_local_tunnel(monkeypatch):
    monkeypatch.setenv("SENTINEL_REDIS_HOST", "192.168.1.116")
    monkeypatch.setenv("SENTINEL_REDIS_PORT", "6379")
    monkeypatch.delenv("GPU_REDIS_HOST", raising=False)
    monkeypatch.delenv("GPU_REDIS_LOCAL_PORT", raising=False)
    assert redis_target_from_env() == ("127.0.0.1", 6380, 0)
    monkeypatch.setenv("GPU_REDIS_HOST", "192.168.1.116")
    with pytest.raises(RuntimeError):
        redis_target_from_env()


def test_health_check_rejects_a_remote_redis(monkeypatch):
    monkeypatch.setenv("GPU_REDIS_HOST", "192.168.1.116")
    assert health_check() == 1


def test_query_gpu_parses_nvidia_smi(monkeypatch):
    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="NVIDIA GeForce GTX 1060 3GB, 3019, 2401\n",
            stderr="",
        )

    monkeypatch.setattr(gpu_probe.subprocess, "run", fake_run)
    monkeypatch.setenv("GPU_WORKER_NAME", "sentinel-gpu-01")
    parsed = query_gpu("0")
    assert parsed["name"] == "NVIDIA GeForce GTX 1060 3GB"
    assert parsed["memory_total"] == 3019
    assert parsed["memory_free"] == 2401
    payload = build_probe_payload("0")
    assert payload["worker"] == "sentinel-gpu-01"
    assert payload["gpu_available"] is True
    assert payload["gpu_memory_total_mb"] == 3019
    assert payload["gpu_memory_free_mb"] == 2401
    assert payload["cuda_visible"] is True
    assert payload["timestamp"]
    assert gpu_system_probe()["gpu_name"] == "NVIDIA GeForce GTX 1060 3GB"


def test_query_gpu_reports_missing_device(monkeypatch):
    def fake_run(*_args, **_kwargs):
        raise FileNotFoundError("nvidia-smi")

    monkeypatch.setattr(gpu_probe.subprocess, "run", fake_run)
    payload = build_probe_payload("0")
    assert payload["gpu_available"] is False
    assert payload["cuda_visible"] is False
    assert payload["gpu_name"] is None


def test_gpu_probe_is_enqueued_only_on_the_gpu_queue(job_redis):
    queued = enqueue_gpu_probe()
    assert queued["enqueued"] is True
    assert queued["queue"] == "gpu"
    assert queued["job_id"] == gpu_probe_job_id()
    job = Job.fetch(gpu_probe_job_id(), connection=job_redis)
    assert job.func_name == "app.jobs.gpu.probe.gpu_system_probe"
    assert job.origin == QUEUE_GPU
    assert gpu_probe_job_id() in Queue(QUEUE_GPU, connection=job_redis).job_ids
    assert gpu_probe_job_id() not in Queue(QUEUE_SEC, connection=job_redis).job_ids
    again = enqueue_gpu_probe()
    assert again["enqueued"] is False


def test_heartbeat_expires_and_probe_can_outlive_it(job_redis):
    record_heartbeat(
        job_redis,
        {
            "name": "sentinel-gpu-01",
            "hostname": "gpu-pc",
            "gpu_name": "NVIDIA GeForce GTX 1060 3GB",
            "gpu_memory_total": 3019,
            "status": "online",
            "last_heartbeat": "2026-10-06T15:00:00+00:00",
            "version": "gpu-worker-v1",
        },
    )
    ttl = job_redis.ttl(worker_key("sentinel-gpu-01"))
    assert 0 < ttl <= HEARTBEAT_TTL_SECONDS
    current = get_worker(job_redis, "sentinel-gpu-01")
    assert current is not None
    assert current["online"] is True
    assert current["gpu_memory_total"] == 3019
    save_probe(
        job_redis,
        "sentinel-gpu-01",
        {"worker": "sentinel-gpu-01", "gpu_available": True, "gpu_name": "NVIDIA GeForce GTX 1060 3GB"},
    )
    job_redis.expire(worker_key("sentinel-gpu-01"), 1)
    time.sleep(1.2)
    expired = get_worker(job_redis, "sentinel-gpu-01")
    assert expired is not None
    assert expired["online"] is False
    assert expired["status"] == "offline"
    assert expired["probe"]["gpu_name"] == "NVIDIA GeForce GTX 1060 3GB"
    assert [row["name"] for row in list_workers(job_redis)] == ["sentinel-gpu-01"]


async def test_gpu_admin_routes_require_jwt_and_roundtrip(client, job_redis):
    assert (await client.post("/admin/jobs/gpu-probe")).status_code == 401
    assert (await client.get("/admin/gpu-workers")).status_code == 401
    assert (await client.get("/admin/gpu-workers/sentinel-gpu-01")).status_code == 401

    headers = await _headers(client, email="gpu-admin@example.com")
    created = await client.post("/admin/jobs/gpu-probe", headers=headers)
    assert created.status_code == 202
    assert created.json()["queue"] == "gpu"
    assert settings.postgres_password not in created.text

    record_heartbeat(
        job_redis,
        {
            "name": "sentinel-gpu-01",
            "hostname": "gpu-pc",
            "gpu_name": "NVIDIA GeForce GTX 1060 3GB",
            "gpu_memory_total": 3019,
            "status": "online",
            "last_heartbeat": "2026-10-06T15:00:00+00:00",
            "version": "gpu-worker-v1",
        },
    )
    listed = await client.get("/admin/gpu-workers", headers=headers)
    assert listed.status_code == 200
    assert listed.json()[0]["name"] == "sentinel-gpu-01"
    assert listed.json()[0]["online"] is True
    detail = await client.get("/admin/gpu-workers/sentinel-gpu-01", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["gpu_memory_total"] == 3019
    assert "postgres://" not in detail.text
    job_redis.delete(worker_key("sentinel-gpu-01"))
    missing = await client.get("/admin/gpu-workers/sentinel-gpu-01", headers=headers)
    assert missing.status_code == 404
    assert (await client.get("/admin/gpu-workers/bad name", headers=headers)).status_code == 404


async def test_admin_page_shows_gpu_worker_in_french(client, monkeypatch):
    monkeypatch.setattr(
        "app.web.viewmodels.gpu_workers_for_page",
        lambda: [
            {
                "name": "sentinel-gpu-01",
                "hostname": "gpu-pc",
                "gpu_name": "NVIDIA GeForce GTX 1060 3GB",
                "gpu_memory_total": 3019,
                "status": "online",
                "last_heartbeat": "2026-10-06T15:00:00+00:00",
                "version": "gpu-worker-v1",
                "online": True,
                "probe": {
                    "gpu_available": True,
                    "gpu_name": "NVIDIA GeForce GTX 1060 3GB",
                    "timestamp": "2026-10-06T15:05:00+00:00",
                },
            }
        ],
    )
    await client.post("/auth/register", json={"email": "gpu-page@example.com", "password": "password123"})
    await client.post(
        "/login",
        data={"email": "gpu-page@example.com", "password": "password123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    page = await client.get("/admin/view")
    assert page.status_code == 200
    assert "Workers GPU" in page.text
    assert "sentinel-gpu-01" in page.text
    assert "En ligne" in page.text
    assert "NVIDIA GeForce GTX 1060 3GB" in page.text
    assert "3019 Mo" in page.text
    assert "Dernier heartbeat" in page.text
    assert "Dernier probe" in page.text
    assert settings.jwt_secret_key not in page.text
    assert settings.postgres_password not in page.text
    refreshed = await client.get("/admin/view", headers={"HX-Request": "true"})
    assert "Workers GPU" in refreshed.text
    row = present_gpu_worker(
        {"name": "offline-worker", "online": False, "status": "offline", "probe": None}
    )
    assert row["presence_label"] == "Hors ligne"
    assert row["probe_label"] == "—"


def test_gpu_deploy_files_are_portable_and_do_not_publish_redis():
    blob = "\n".join(path.read_text() for path in _DEPLOY.rglob("*") if path.is_file() and path.suffix != ".gguf")
    assert "/mnt/c" not in blob
    assert "C:\\" not in blob
    assert "0.0.0.0" not in blob
    dockerfile = (_DEPLOY / "Dockerfile").read_text()
    assert "nvidia/cuda:11.8.0-devel-ubuntu22.04" in dockerfile
    assert "nvidia/cuda:11.8.0-runtime-ubuntu22.04" in dockerfile
    assert "CMAKE_CUDA_ARCHITECTURES=61" in dockerfile
    assert "cuda:13" not in dockerfile.lower()
    compose = (_DEPLOY / "docker-compose.yml").read_text()
    assert "network_mode: host" in compose
    assert "sentinel-gpu-worker" in compose
    assert "GPU_REDIS_HOST: 127.0.0.1" in compose
    assert "AI_MODEL_CACHE: /models" in compose
    assert "postgres" not in compose.lower()
    core = Path("docker-compose.yml").read_text()
    assert '"127.0.0.1:6379:6379"' in core
    for name in ("install.sh", "check-gpu.sh", "healthcheck.sh", "tunnel.sh", "env.sh", "fetch-model.sh", "test-model.sh"):
        subprocess.run(["bash", "-n", str(_DEPLOY / name)], check=True)
