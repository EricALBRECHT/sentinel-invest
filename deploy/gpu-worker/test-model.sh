#!/usr/bin/env bash
# Local GPU model smoke test (run on sentinel-gpu-01).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/env.sh"

CACHE="$(read_env AI_MODEL_CACHE /home/eric/sentinel-models)"
MODEL_NAME="$(read_env AI_MODEL_NAME Qwen2.5-1.5B-Instruct-Q4_K_M)"
ALIAS="${CACHE}/${MODEL_NAME}.gguf"
OFFICIAL="${CACHE}/qwen2.5-1.5b-instruct-q4_k_m.gguf"

if [[ ! -f "${ALIAS}" && ! -f "${OFFICIAL}" ]]; then
  echo "GGUF missing. Run ./fetch-model.sh first." >&2
  exit 1
fi

echo "=== nvidia-smi ==="
nvidia-smi || { echo "CUDA/driver unavailable" >&2; exit 1; }

echo "=== container load test ==="
docker exec -i sentinel-gpu-worker python3 - <<'PY'
import json, os, time
from app.jobs.gpu.model_runtime import (
    detect_cuda_build,
    env_gpu_layers,
    env_model_path,
    query_vram_mb,
    runtime_status,
    warmup_model,
    generate_structured_output,
)

print("model_path", env_model_path())
print("cuda_build", json.dumps(detect_cuda_build(), default=str))
before = query_vram_mb()
print("vram_before", before)
started = time.monotonic()
warm = warmup_model()
print("warmup", json.dumps(warm, default=str))
status = runtime_status()
print("status", json.dumps(status, default=str))
after_load = query_vram_mb()
print("vram_after_load", after_load)
payload = {
    "document_id": 0,
    "title": "Smoke test",
    "published_at": None,
    "source_name": "test",
    "source_type": "OTHER",
    "trust_level": "LOW",
    "company_context": [{"company_id": 1, "name": "NVIDIA Corporation", "ticker": "NVDA", "relation_type": "SUBJECT"}],
    "content_text": "NVIDIA announced a partnership to expand datacenter GPU capacity.",
    "content_truncated": False,
    "original_char_count": 70,
    "analyzed_char_count": 70,
}
raw, parsed, meta = generate_structured_output(payload)
elapsed_ms = int((time.monotonic() - started) * 1000)
after = query_vram_mb()
print("backend", meta.get("backend") or status.get("model_backend"))
print("gpu_layers", status.get("gpu_layers") or env_gpu_layers())
print("duration_ms", elapsed_ms)
print("vram_after_infer", after)
print("tokens", meta.get("tokens_input"), meta.get("tokens_output"))
print("json", json.dumps(parsed, ensure_ascii=False)[:2000])
PY
