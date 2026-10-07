#!/usr/bin/env bash
# Run ON sentinel-gpu-01. Sets AI_MAX_CONTEXT and restarts the GPU worker
# so llama.cpp reloads with the new n_ctx (same model, same GPU layers).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "${ROOT}"

CTX="${1:?usage: ./set-ai-context.sh 4096}"
if ! [[ "$CTX" =~ ^[0-9]+$ ]]; then
  echo "context must be an integer" >&2
  exit 1
fi

python3 - <<PY
from pathlib import Path
path = Path(".env")
text = path.read_text() if path.exists() else ""
key = "AI_MAX_CONTEXT"
value = "${CTX}"
lines = text.splitlines()
out = []
found = False
for line in lines:
    if line.startswith(f"{key}="):
        out.append(f"{key}={value}")
        found = True
    else:
        out.append(line)
if not found:
    out.append(f"{key}={value}")
path.write_text("\n".join(out) + ("\n" if out else ""))
print(f"set {key}={value}")
PY

docker compose up -d --force-recreate gpu-worker
./healthcheck.sh
docker exec sentinel-gpu-worker python3 - <<PY
from app.jobs.gpu.model_runtime import preload_model, runtime_status, query_vram_mb
before = query_vram_mb()
print("vram_before_load", before)
payload = preload_model()
status = runtime_status()
after = query_vram_mb()
print("preload", {k: payload.get(k) for k in ("ok", "backend", "gpu_layers", "status", "error")})
print("context_size", status.get("context_size"))
print("vram_after_load", after)
print("model_memory_mb", status.get("model_memory_mb"))
print("backend", status.get("model_backend"), "layers", status.get("gpu_layers"))
PY
