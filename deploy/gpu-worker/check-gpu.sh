#!/usr/bin/env bash
# Host checks before the worker starts. Does not change Redis or PostgreSQL.
set -euo pipefail
cd "$(dirname "$0")"

case "$(pwd)" in
  /mnt/*)
    echo "Refus : le dépôt est sous /mnt. Utilisez le filesystem Linux." >&2
    exit 1
    ;;
esac

fail() {
  echo "$1" >&2
  exit 1
}

command -v nvidia-smi >/dev/null 2>&1 || fail "nvidia-smi introuvable. Installez le driver NVIDIA sur l'hôte."
command -v docker >/dev/null 2>&1 || fail "docker introuvable."
docker compose version >/dev/null 2>&1 || fail "docker compose introuvable."

echo "Driver hôte :"
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader

image="$(sed -n 's/^FROM //p' Dockerfile | head -n 1)"
if [ -z "$image" ]; then
  fail "Image CUDA introuvable dans le Dockerfile."
fi
echo "Test conteneur : ${image}"
docker run --rm --gpus all "$image" nvidia-smi
echo "GPU visible dans un conteneur."
