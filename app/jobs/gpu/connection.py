"""Redis through the local SSH tunnel. The GPU worker never dials the LAN."""

import os

from redis import Redis

_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost"})


def redis_target_from_env() -> tuple[str, int, int]:
    host = os.environ.get("GPU_REDIS_HOST", "127.0.0.1")
    port = int(os.environ.get("GPU_REDIS_LOCAL_PORT", "6380"))
    database = int(os.environ.get("SENTINEL_REDIS_DB", "0"))
    if host not in _LOCAL_HOSTS:
        raise RuntimeError("GPU worker Redis must use the local SSH tunnel")
    return host, port, database


def redis_from_env() -> Redis:
    host, port, database = redis_target_from_env()
    return Redis(
        host=host,
        port=port,
        db=database,
        socket_keepalive=True,
        socket_connect_timeout=2,
        health_check_interval=30,
    )
