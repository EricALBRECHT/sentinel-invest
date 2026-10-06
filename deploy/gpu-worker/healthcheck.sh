#!/usr/bin/env bash
# Operator check: host GPU, local tunnel, container health.
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck source=env.sh
source ./env.sh

local_port="$(read_env GPU_REDIS_LOCAL_PORT 6380)"
nvidia-smi -L

if ! timeout 3 bash -c "echo >/dev/tcp/127.0.0.1/${local_port}" 2>/dev/null; then
  echo "Le tunnel n'écoute pas sur 127.0.0.1:${local_port}" >&2
  exit 1
fi
echo "tunnel 127.0.0.1:${local_port} ouvert"

status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' sentinel-gpu-worker)"
echo "sentinel-gpu-worker: ${status}"
[ "$status" = "healthy" ]
