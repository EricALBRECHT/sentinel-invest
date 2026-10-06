#!/bin/bash
# Isolated llama.cpp inference diagnosis for the GTX 1060 worker.
# Does not start RQ and does not enqueue documents.
# CUDA_LAUNCH_BLOCKING is used only on a follow-up crash run.
set -uo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
cd "$ROOT"
OUT="/tmp/sentinel-diagnose"
PYFILE="$OUT/diagnose_one.py"
mkdir -p "$OUT"
exec 9>"$OUT/lock"
flock -n 9 || { echo "diagnose-inference already running"; exit 1; }
docker rm -f sentinel-gpu-diagnose >/dev/null 2>&1 || true

read_env() {
  python3 - "$1" <<'PY'
import sys
from pathlib import Path
key = sys.argv[1]
path = Path(".env")
if not path.exists():
    raise SystemExit(0)
for line in path.read_text(encoding="utf-8").splitlines():
    if line.startswith(key + "="):
        print(line.split("=", 1)[1].strip().strip('"').strip("'"))
        break
PY
}

GPU_DEVICE="$(read_env GPU_DEVICE)"
GPU_DEVICE="${GPU_DEVICE:-0}"
AI_MODEL_CACHE="$(read_env AI_MODEL_CACHE)"
AI_MODEL_NAME="$(read_env AI_MODEL_NAME)"
AI_MODEL_NAME="${AI_MODEL_NAME:-Qwen2.5-1.5B-Instruct-Q4_K_M}"
if [ -z "${AI_MODEL_CACHE}" ]; then
  echo "AI_MODEL_CACHE is missing from deploy/gpu-worker/.env" >&2
  exit 1
fi

smi() {
  if [ -n "${SMI_BIN:-}" ]; then
    "$SMI_BIN" --query-gpu=memory.used,memory.free,utilization.gpu,power.draw --format=csv,noheader,nounits -i "$GPU_DEVICE" 2>/dev/null | head -n 1 || true
    return 0
  fi
  docker run --rm --gpus "device=${GPU_DEVICE}" --entrypoint nvidia-smi sentinel-gpu-worker \
    --query-gpu=memory.used,memory.free,utilization.gpu,power.draw --format=csv,noheader,nounits -i 0 2>/dev/null | head -n 1 || true
  return 0
}

pick_smi() {
  local candidate
  for candidate in nvidia-smi /usr/bin/nvidia-smi /usr/lib/wsl/lib/nvidia-smi; do
    if command -v "$candidate" >/dev/null 2>&1 || [ -x "$candidate" ]; then
      SMI_BIN="$candidate"
      return
    fi
  done
  SMI_BIN=""
}

cat >"$PYFILE" <<'PY'
import inspect
import os
import subprocess
import sys
import time

from llama_cpp import Llama


def gpu_query():
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.free,utilization.gpu,power.draw",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "unavailable:%s" % type(exc).__name__
    if completed.returncode != 0:
        return "unavailable"
    lines = [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]
    return lines[0] if lines else "unavailable"


model_path = os.environ["MODEL_PATH"]
n_gpu_layers = int(os.environ["N_GPU_LAYERS"])
n_ctx = int(os.environ["N_CTX"])
n_batch = int(os.environ["N_BATCH"])
n_ubatch = int(os.environ["N_UBATCH"])
max_tokens = int(os.environ["MAX_TOKENS"])
backend = "CUDA" if n_gpu_layers > 0 else "CPU"

print("DIAG_GPU_LAYERS=%s" % n_gpu_layers, flush=True)
print("DIAG_CTX=%s" % n_ctx, flush=True)
print("DIAG_BATCH=%s" % n_batch, flush=True)
print("DIAG_UBATCH=%s" % n_ubatch, flush=True)
print("DIAG_BACKEND=%s" % backend, flush=True)
print("DIAG_MMQ=%s" % os.environ.get("GGML_CUDA_FORCE_MMQ", "unset"), flush=True)
print("DIAG_LAUNCH_BLOCKING=%s" % os.environ.get("CUDA_LAUNCH_BLOCKING", "unset"), flush=True)
print("DIAG_VRAM_BEFORE=%s" % gpu_query(), flush=True)

signature = inspect.signature(Llama.__init__)
kwargs = {
    "model_path": model_path,
    "n_gpu_layers": n_gpu_layers,
    "n_ctx": n_ctx,
    "n_batch": n_batch,
    "verbose": True,
}
if "n_ubatch" in signature.parameters:
    kwargs["n_ubatch"] = n_ubatch
else:
    print("DIAG_UBATCH_UNSUPPORTED=1", flush=True)
if "flash_attn" in signature.parameters:
    kwargs["flash_attn"] = False
else:
    print("DIAG_FLASH_ATTN_UNSUPPORTED=1", flush=True)

started = time.perf_counter()
llm = Llama(**kwargs)
print("DIAG_LOAD_SECONDS=%.3f" % (time.perf_counter() - started), flush=True)
print("DIAG_VRAM_AFTER_LOAD=%s" % gpu_query(), flush=True)
decode_started = time.perf_counter()
response = llm.create_chat_completion(
    messages=[{"role": "user", "content": 'Reply only with: {"ok": true}'}],
    temperature=0,
    max_tokens=max_tokens,
)
print("DIAG_DECODE_SECONDS=%.3f" % (time.perf_counter() - decode_started), flush=True)
choice = (response.get("choices") or [{}])[0]
message = choice.get("message") or {}
content = message.get("content")
text = content if isinstance(content, str) else ""
print("DIAG_RESULT_BEGIN", flush=True)
sys.stdout.write(text)
print("\nDIAG_RESULT_END", flush=True)
PY

field() {
  local key="$1" file="$2"
  grep -m1 "^${key}=" "$file" 2>/dev/null | cut -d= -f2- || true
}

wait_vram_settled() {
  local attempt used
  for attempt in 1 2 3 4 5 6 7 8 9 10 11 12; do
    used="$(smi | awk -F, '{gsub(/ /,"",$1); print $1}')"
    echo "vram_used_mb=${used:-unknown} attempt=${attempt} baseline=${VRAM_BASELINE:-unknown}"
    if [ -n "${VRAM_BASELINE:-}" ] && [ -n "${used:-}" ]; then
      python3 - "$used" "$VRAM_BASELINE" <<'PY'
import sys
used = float(sys.argv[1])
base = float(sys.argv[2])
sys.exit(0 if used <= base + 250 else 1)
PY
      if [ $? -eq 0 ]; then
        return 0
      fi
    elif [ -n "${used:-}" ]; then
      return 0
    fi
    sleep 3
  done
  echo "VRAM did not return near the baseline" >&2
  return 1
}

run_one() {
  local layers="$1" ctx="$2" batch="$3" ubatch="$4" mmq="$5" blocking="$6" label="$7"
  local logfile="$OUT/${label}.log"
  local before after start_s end_s elapsed ec sig result err load decode vram_before vram_after
  if docker ps --format '{{.Names}}' | grep -qx sentinel-gpu-worker; then
    echo "refusing to load a second model while sentinel-gpu-worker is running" >&2
    exit 1
  fi
  before="$(smi || true)"
  echo "RUN ${label} gpu_layers=${layers} ctx=${ctx} batch=${batch} ubatch=${ubatch} mmq=${mmq} launch_blocking=${blocking}"
  echo "nvidia-smi before: ${before}"
  start_s=$(date +%s)
  local -a extra=()
  if [ "$mmq" != "unset" ]; then
    extra+=(-e "GGML_CUDA_FORCE_MMQ=${mmq}")
  fi
  if [ "$blocking" != "unset" ]; then
    extra+=(-e "CUDA_LAUNCH_BLOCKING=${blocking}")
  fi
  set +e
  timeout --signal=TERM -k 20 240 docker run --rm --name sentinel-gpu-diagnose \
    --gpus "device=${GPU_DEVICE}" \
    --entrypoint python3 \
    -e PYTHONUNBUFFERED=1 \
    -e "N_GPU_LAYERS=${layers}" \
    -e "N_CTX=${ctx}" \
    -e "N_BATCH=${batch}" \
    -e "N_UBATCH=${ubatch}" \
    -e MAX_TOKENS=8 \
    -e "MODEL_PATH=${MODEL_PATH}" \
    "${extra[@]}" \
    -v "${AI_MODEL_CACHE}:/models:ro" \
    -v "${PYFILE}:/diagnose_one.py:ro" \
    sentinel-gpu-worker /diagnose_one.py >"$logfile" 2>&1
  ec=$?
  docker rm -f sentinel-gpu-diagnose >/dev/null 2>&1 || true
  set -e
  end_s=$(date +%s)
  elapsed=$((end_s - start_s))
  after="$(smi || true)"
  sig=""
  if [ "$ec" -ge 128 ] && [ "$ec" -lt 160 ]; then
    sig=$((ec - 128))
  fi
  result="$(awk 'flag && !/^DIAG_RESULT_END/{print} /^DIAG_RESULT_BEGIN/{flag=1} /^DIAG_RESULT_END/{flag=0}' "$logfile" | tr '\n' ' ' | sed 's/[[:space:]]\+/ /g' | cut -c1-120)"
  load="$(field DIAG_LOAD_SECONDS "$logfile")"
  decode="$(field DIAG_DECODE_SECONDS "$logfile")"
  vram_before="$(field DIAG_VRAM_BEFORE "$logfile")"
  vram_after="$(field DIAG_VRAM_AFTER_LOAD "$logfile")"
  err="$(grep -E "CUDA error|out of memory|GGML_ASSERT|invalid device|misaligned|no kernel image" "$logfile" | tail -n 1 || true)"
  if [ "$ec" -eq 0 ] && [ -n "$result" ]; then
    status="OK"
  else
    status="FAIL"
  fi
  printf '%s\x1f%s\x1f%s\x1f%s\x1f%s\x1f%s\x1f%s\x1f%s\x1f%s\x1f%s\x1f%s\x1f%s\x1f%s\x1f%s\x1f%s\n' \
    "$label" "$layers" "$ctx" "$batch" "$mmq" "$status" "$elapsed" "${load:-}" "${decode:-}" \
    "${vram_before:-$before}" "${vram_after:-}" "$ec" "${sig:-}" "$result" "$err" \
    >>"$OUT/results.tsv"
  echo "exit=${ec} signal=${sig:-none} status=${status} wall_s=${elapsed} load_s=${load:-na} decode_s=${decode:-na}"
  echo "nvidia-smi after: ${after}"
  echo "vram_before_load=${vram_before:-na} vram_after_load=${vram_after:-na} result=${result:-<empty>}"
  if [ "$status" != "OK" ]; then
    echo "----- log tail ${label} -----"
    tail -n 30 "$logfile"
  fi
  wait_vram_settled || true
}

pick_smi
echo "smi_bin=${SMI_BIN:-docker-nvidia-smi}"
echo "probe $(smi || echo 'nvidia-smi unavailable')"

if docker ps --format '{{.Names}}' | grep -qx sentinel-gpu-worker; then
  echo "===== QUEUE BEFORE STOP ====="
  docker exec sentinel-gpu-worker python3 - <<'PY' || true
from redis import Redis
from rq import Queue
connection = Redis(host="127.0.0.1", port=6380, socket_connect_timeout=3)
queue = Queue("gpu", connection=connection)
print("queued_ids", list(queue.job_ids))
PY
  echo "stopping sentinel-gpu-worker so a single model instance can run"
  docker stop -t 30 sentinel-gpu-worker
fi

pick_smi
VRAM_BASELINE="$(smi | awk -F, '{gsub(/ /,"",$1); print $1}')"
echo "vram_baseline_mb=${VRAM_BASELINE:-unknown}"
wait_vram_settled || true

mapfile -t GGUF < <(docker run --rm --entrypoint python3 -v "${AI_MODEL_CACHE}:/models:ro" sentinel-gpu-worker -c 'import os; print("\n".join(sorted(name for name in os.listdir("/models") if name.endswith(".gguf"))))')
MODEL_FILE=""
if [ "${#GGUF[@]}" -eq 0 ]; then
  echo "no GGUF found under the model cache" >&2
  exit 1
fi
for name in "${GGUF[@]}"; do
  if [ "$name" = "${AI_MODEL_NAME}.gguf" ]; then
    MODEL_FILE="$name"
  fi
done
if [ -z "$MODEL_FILE" ] && [ "${#GGUF[@]}" -gt 0 ]; then
  MODEL_FILE="${GGUF[0]}"
fi
if [ -z "$MODEL_FILE" ]; then
  echo "no GGUF found under the model cache" >&2
  exit 1
fi
MODEL_PATH="/models/${MODEL_FILE}"
echo "model_file=${MODEL_FILE}"

: >"$OUT/results.tsv"
CPU_OK=0
GPU_OK=0
GPU_FAIL=0
FIRST_GPU_FAIL=""

for layers in 0 1 5 10 15 20; do
  run_one "$layers" 1024 64 64 unset unset "layers-${layers}"
  state="$(python3 -c 'import pathlib,sys; line=pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()[-1]; print(line.split("\x1f")[5])' "$OUT/results.tsv")"
  if [ "$layers" -eq 0 ]; then
    if [ "$state" != "OK" ]; then
      echo "CPU baseline failed. GPU layers will not be treated as a CUDA result."
      CPU_OK=0
      break
    fi
    CPU_OK=1
  elif [ "$state" = "OK" ]; then
    GPU_OK=$((GPU_OK + 1))
  else
    GPU_FAIL=$((GPU_FAIL + 1))
    if [ -z "$FIRST_GPU_FAIL" ]; then
      FIRST_GPU_FAIL="$layers"
    fi
  fi
done

if [ "$CPU_OK" -eq 1 ] && [ -n "$FIRST_GPU_FAIL" ]; then
  echo "===== CUDA_LAUNCH_BLOCKING diagnostic for gpu_layers=${FIRST_GPU_FAIL} ====="
  run_one "$FIRST_GPU_FAIL" 1024 64 64 unset 1 "layers-${FIRST_GPU_FAIL}-blocking"
  echo "===== GGML_CUDA_FORCE_MMQ=1 for gpu_layers=${FIRST_GPU_FAIL} ====="
  run_one "$FIRST_GPU_FAIL" 1024 64 64 1 unset "layers-${FIRST_GPU_FAIL}-mmq"
fi

LAYER20="$(python3 -c 'import pathlib,sys; rows=[line.split("\x1f") for line in pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").splitlines() if line.strip()]; print(next((row[5] for row in rows if row[0]=="layers-20"), ""))' "$OUT/results.tsv")"
if [ "$CPU_OK" -eq 1 ] && [ "$LAYER20" = "OK" ]; then
  echo "===== production-shape control ctx=2048 batch=512 layers=20 ====="
  run_one 20 2048 512 512 unset unset "production-shape-20"
fi

echo "===== TABLE ====="
python3 - "$OUT/results.tsv" <<'PY'
import sys
from pathlib import Path
headers = ["label","gpu_layers","ctx","batch","MMQ","result","wall_s","load_s","decode_s","vram_before","vram_after_load","exit","signal","text","error"]
print(" | ".join(headers))
for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    fields = line.split("\x1f")
    while len(fields) < len(headers):
        fields.append("")
    print(" | ".join(field.replace("\n", " ") for field in fields[:len(headers)]))
PY

MMQ_STATE="$(python3 -c 'import pathlib,sys; rows=[line.split("\x1f") for line in pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").splitlines() if line.strip()]; print(next((row[5] for row in rows if row[0].endswith("-mmq")), ""))' "$OUT/results.tsv")"
PROD_STATE="$(python3 -c 'import pathlib,sys; rows=[line.split("\x1f") for line in pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").splitlines() if line.strip()]; print(next((row[5] for row in rows if row[0]=="production-shape-20"), ""))' "$OUT/results.tsv")"
echo "===== SITUATION ====="
if [ "$CPU_OK" -ne 1 ]; then
  echo "CPU baseline failed. The failure is not specific to CUDA."
elif [ "$GPU_FAIL" -eq 0 ] && [ "$PROD_STATE" = "FAIL" ]; then
  echo "D. CPU and conservative GPU layers work. The production shape ctx=2048 batch=512 crashes."
elif [ "$GPU_FAIL" -eq 0 ] && [ "$PROD_STATE" = "OK" ]; then
  echo "Conservative GPU settings work, and the production shape also produced text on this minimal prompt."
elif [ "$GPU_OK" -eq 0 ]; then
  if [ "$MMQ_STATE" = "OK" ]; then
    echo "C. No default GPU layer worked. GGML_CUDA_FORCE_MMQ=1 produced text."
  else
    echo "A. CPU works. No tested GPU layer produced text. MMQ did not fix the failing layer."
  fi
else
  if [ "$MMQ_STATE" = "OK" ]; then
    echo "B and C. Some GPU layers work. MMQ produced text on a layer that failed with the default kernels."
  else
    echo "B. CPU works. Some GPU layers work and higher layer counts fail."
  fi
fi
echo "worker_left_stopped=sentinel-gpu-worker"
echo "document_3_not_relaunched=1"
