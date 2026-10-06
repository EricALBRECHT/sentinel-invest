"""Heartbeat and last probe stored in Redis. Keys expire on their own."""

import json
import re
from typing import Any

from redis import Redis

from app.jobs.gpu import WORKER_VERSION

HEARTBEAT_INTERVAL_SECONDS = 30
HEARTBEAT_TTL_SECONDS = 120
PROBE_TTL_SECONDS = 7 * 24 * 3600
WORKER_KEY_PREFIX = "sentinel:gpu-worker:"
PROBE_KEY_PREFIX = "sentinel:gpu-probe:"
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def worker_key(name: str) -> str:
    _require_name(name)
    return f"{WORKER_KEY_PREFIX}{name}"


def probe_key(name: str) -> str:
    _require_name(name)
    return f"{PROBE_KEY_PREFIX}{name}"


def record_heartbeat(connection: Redis, payload: dict[str, Any]) -> None:
    name = str(payload["name"])
    body = {
        "name": name,
        "hostname": payload.get("hostname"),
        "gpu_name": payload.get("gpu_name"),
        "gpu_memory_total": payload.get("gpu_memory_total"),
        "status": payload.get("status") or "online",
        "last_heartbeat": payload.get("last_heartbeat"),
        "version": payload.get("version") or WORKER_VERSION,
    }
    connection.set(worker_key(name), json.dumps(body), ex=HEARTBEAT_TTL_SECONDS)


def save_probe(connection: Redis, name: str, payload: dict[str, Any]) -> None:
    connection.set(probe_key(name), json.dumps(payload), ex=PROBE_TTL_SECONDS)


def get_worker(connection: Redis, name: str) -> dict[str, Any] | None:
    _require_name(name)
    heartbeat = _load(connection.get(worker_key(name)))
    probe = _load(connection.get(probe_key(name)))
    if heartbeat is None and probe is None:
        return None
    if heartbeat is None:
        return {
            "name": name,
            "hostname": None,
            "gpu_name": None,
            "gpu_memory_total": None,
            "status": "offline",
            "last_heartbeat": None,
            "version": None,
            "online": False,
            "probe": probe,
        }
    heartbeat["online"] = True
    heartbeat["probe"] = probe
    return heartbeat


def list_workers(connection: Redis) -> list[dict[str, Any]]:
    names: set[str] = set()
    for key in _scan(connection, f"{WORKER_KEY_PREFIX}*"):
        names.add(key.removeprefix(WORKER_KEY_PREFIX))
    for key in _scan(connection, f"{PROBE_KEY_PREFIX}*"):
        names.add(key.removeprefix(PROBE_KEY_PREFIX))
    rows = [get_worker(connection, name) for name in sorted(names)]
    return [row for row in rows if row is not None]


def _require_name(name: str) -> None:
    if not _NAME.fullmatch(name):
        raise ValueError("Invalid GPU worker name")


def _load(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode()
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(loaded, dict):
        return None
    return loaded


def _scan(connection: Redis, pattern: str) -> list[str]:
    found: list[str] = []
    for key in connection.scan_iter(match=pattern, count=100):
        if isinstance(key, bytes):
            key = key.decode()
        found.append(str(key))
    return found
