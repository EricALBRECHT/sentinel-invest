#!/usr/bin/env bash
# Download the official Qwen2.5-1.5B-Instruct Q4_K_M GGUF into AI_MODEL_CACHE.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/env.sh"

CACHE="$(read_env AI_MODEL_CACHE /home/eric/sentinel-models)"
OFFICIAL_NAME="qwen2.5-1.5b-instruct-q4_k_m.gguf"
ALIAS_NAME="Qwen2.5-1.5B-Instruct-Q4_K_M.gguf"
REPO="Qwen/Qwen2.5-1.5B-Instruct-GGUF"
URL="https://huggingface.co/${REPO}/resolve/main/${OFFICIAL_NAME}"
LICENSE_URL="https://huggingface.co/${REPO}/resolve/main/LICENSE"

mkdir -p "${CACHE}"
target_official="${CACHE}/${OFFICIAL_NAME}"
target_alias="${CACHE}/${ALIAS_NAME}"

if [[ ! -f "${target_official}" ]]; then
  echo "Downloading ${OFFICIAL_NAME} from ${REPO} ..."
  curl -fL --progress-bar -o "${target_official}.partial" "${URL}"
  mv "${target_official}.partial" "${target_official}"
else
  echo "Already present: ${target_official}"
fi

if [[ ! -e "${target_alias}" ]]; then
  ln -s "${OFFICIAL_NAME}" "${target_alias}"
  echo "Alias created: ${target_alias} -> ${OFFICIAL_NAME}"
fi

size="$(stat -c '%s' "${target_official}" 2>/dev/null || stat -f '%z' "${target_official}")"
sha="$(sha256sum "${target_official}" | awk '{print $1}')"
EXPECTED_SHA="6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e"
EXPECTED_SIZE="1117320736"
if [[ "${size}" != "${EXPECTED_SIZE}" ]]; then
  echo "Unexpected size ${size} (expected ${EXPECTED_SIZE})" >&2
  exit 1
fi
if [[ "${sha}" != "${EXPECTED_SHA}" ]]; then
  echo "Unexpected SHA256 ${sha} (expected ${EXPECTED_SHA})" >&2
  exit 1
fi
echo "source_repo=${REPO}"
echo "official_file=${OFFICIAL_NAME}"
echo "alias_file=${ALIAS_NAME}"
echo "size_bytes=${size}"
echo "sha256=${sha}"
echo "license_url=${LICENSE_URL}"
echo "license=Apache-2.0 (see LICENSE in the Hugging Face repo)"
echo "MODEL_READY=${target_alias}"
