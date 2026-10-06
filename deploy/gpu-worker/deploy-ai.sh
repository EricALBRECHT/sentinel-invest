#!/usr/bin/env bash
# Deploy GPU worker with local Qwen GGUF inference (run ON sentinel-gpu-01).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "${ROOT}"

chmod +x fetch-model.sh test-model.sh healthcheck.sh check-gpu.sh tunnel.sh install.sh 2>/dev/null || true

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example — edit AI_MODEL_CACHE if needed."
fi

# Ensure host cache path and GGUF
./fetch-model.sh

# Sync AI settings into .env without wiping custom keys
python3 - <<'PY'
from pathlib import Path
path = Path(".env")
text = path.read_text()
updates = {
    "AI_PROVIDER": "local",
    "AI_MODEL_NAME": "Qwen2.5-1.5B-Instruct-Q4_K_M",
    "AI_MODEL_CACHE": "/home/eric/sentinel-models",
    "AI_GPU_LAYERS": "20",
    "AI_MAX_CONTEXT": "2048",
    "AI_MAX_OUTPUT_TOKENS": "768",
    "AI_TEMPERATURE": "0",
    "SENTINEL_AI_STUB": "0",
}
lines = text.splitlines()
keys = set()
out = []
for line in lines:
    if not line.strip() or line.strip().startswith("#") or "=" not in line:
        out.append(line)
        continue
    key = line.split("=", 1)[0].strip()
    keys.add(key)
    if key in updates:
        out.append(f"{key}={updates[key]}")
    else:
        out.append(line)
for key, value in updates.items():
    if key not in keys:
        out.append(f"{key}={value}")
path.write_text("\n".join(out) + "\n")
print("Updated .env AI settings")
PY

docker compose build
docker compose up -d
./healthcheck.sh
./test-model.sh
