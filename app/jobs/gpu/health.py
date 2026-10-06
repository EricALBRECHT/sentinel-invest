"""Container healthcheck: the tunnel answers, and the GPU answers."""

import os

from app.jobs.gpu.connection import redis_from_env, redis_target_from_env
from app.jobs.gpu.probe import query_gpu


def health_check() -> int:
    try:
        redis_target_from_env()
        connection = redis_from_env()
        if not connection.ping():
            return 1
        gpu = query_gpu(os.environ.get("GPU_DEVICE", "0"))
    except Exception:
        return 1
    return 0 if gpu["available"] else 1


def main() -> None:
    raise SystemExit(health_check())


if __name__ == "__main__":
    main()
