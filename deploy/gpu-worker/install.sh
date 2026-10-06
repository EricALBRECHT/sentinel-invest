#!/usr/bin/env bash
# Idempotent setup for the GPU worker. Does not open Redis, sudo, or edit WSL config.
set -euo pipefail
cd "$(dirname "$0")"
root="$(pwd)"

case "$root" in
  /mnt/*)
    echo "Refus : le dépôt est sous /mnt. Clonez-le sur le filesystem Linux." >&2
    exit 1
    ;;
esac

# shellcheck source=env.sh
source ./env.sh

if ! command -v docker >/dev/null 2>&1; then
  echo "docker introuvable." >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "docker compose introuvable." >&2
  exit 1
fi
if ! command -v nvidia-smi >/dev/null 2>&1; then
  echo "nvidia-smi introuvable. Installez le driver NVIDIA avant ce script." >&2
  exit 1
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo ".env créé depuis .env.example. Relisez-le, puis relancez ./install.sh." >&2
  exit 1
fi

cache="$(read_env AI_MODEL_CACHE /models)"
if [ ! -d "$cache" ]; then
  if ! mkdir -p "$cache" 2>/dev/null; then
    echo "Impossible de créer ${cache}. Réglez AI_MODEL_CACHE vers un dossier accessible." >&2
    exit 1
  fi
fi

key="$(read_env SENTINEL_SSH_KEY "")"
if [ -z "$key" ]; then
  key="${HOME}/.ssh/sentinel_gpu"
fi
mkdir -p "$(dirname "$key")" "${HOME}/.ssh"
chmod 700 "${HOME}/.ssh"
if [ ! -f "$key" ]; then
  ssh-keygen -t ed25519 -f "$key" -N "" -C "sentinel-gpu-worker"
  echo "Clé créée : ${key}.pub"
fi
chmod 600 "$key"

host="$(read_env SENTINEL_CORE_HOST "")"
ssh_user="$(read_env SENTINEL_CORE_SSH_USER sentinel)"
ssh_port="$(read_env SENTINEL_CORE_SSH_PORT 22)"
if [ -z "$host" ]; then
  echo "SENTINEL_CORE_HOST est vide." >&2
  exit 1
fi

known="${HOME}/.ssh/known_hosts"
touch "$known"
chmod 600 "$known"
if ! ssh-keygen -F "$host" -f "$known" >/dev/null 2>&1; then
  echo "Enregistrement de la clé d'hôte de ${host}"
  ssh-keyscan -H -p "$ssh_port" "$host" >> "$known"
fi

if ! ssh -i "$key" -p "$ssh_port" \
  -o BatchMode=yes \
  -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile="$known" \
  "${ssh_user}@${host}" true
then
  echo "La clé publique n'est pas autorisée sur ${ssh_user}@${host}." >&2
  echo "Copiez-la, puis relancez ./install.sh :" >&2
  echo "  ssh-copy-id -i ${key}.pub -p ${ssh_port} ${ssh_user}@${host}" >&2
  exit 1
fi

./check-gpu.sh

unit_dir="${HOME}/.config/systemd/user"
mkdir -p "$unit_dir"
awk -v script="${root}/tunnel.sh" '{gsub(/__TUNNEL_SCRIPT__/, script); print}' \
  systemd/sentinel-gpu-tunnel.service > "${unit_dir}/sentinel-gpu-tunnel.service"

if systemctl --user show-environment >/dev/null 2>&1; then
  systemctl --user daemon-reload
  systemctl --user enable sentinel-gpu-tunnel.service
  systemctl --user restart sentinel-gpu-tunnel.service
else
  echo "systemd --user est indisponible. Le tunnel est lancé en arrière-plan."
  echo "Ubuntu Server et WSL2 avec systemd=true utilisent le service utilisateur au prochain lancement."
  mkdir -p run
  if [ -f run/tunnel.pid ] && kill -0 "$(cat run/tunnel.pid)" 2>/dev/null; then
    echo "Tunnel déjà actif."
  else
    nohup ./tunnel.sh >> run/tunnel.log 2>&1 &
    echo $! > run/tunnel.pid
  fi
fi

local_port="$(read_env GPU_REDIS_LOCAL_PORT 6380)"
ready=0
for _ in $(seq 1 20); do
  if timeout 1 bash -c "echo >/dev/tcp/127.0.0.1/${local_port}" 2>/dev/null; then
    ready=1
    break
  fi
  sleep 0.5
done
if [ "$ready" -ne 1 ]; then
  echo "Le tunnel n'écoute pas sur 127.0.0.1:${local_port}" >&2
  exit 1
fi

docker compose up -d --build
echo "sentinel-gpu-worker démarré. Contrôle : ./healthcheck.sh"
