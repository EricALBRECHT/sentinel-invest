#!/usr/bin/env bash
# Persistent SSH local forward: GPU localhost:6380 -> core 127.0.0.1:6379.
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck source=env.sh
source ./env.sh

host="$(read_env SENTINEL_CORE_HOST "")"
remote_port="$(read_env SENTINEL_REDIS_PORT 6379)"
local_port="$(read_env GPU_REDIS_LOCAL_PORT 6380)"
ssh_user="$(read_env SENTINEL_CORE_SSH_USER sentinel)"
ssh_port="$(read_env SENTINEL_CORE_SSH_PORT 22)"
key="$(read_env SENTINEL_SSH_KEY "")"
if [ -z "$host" ]; then
  echo "SENTINEL_CORE_HOST est vide." >&2
  exit 1
fi
if [ -z "$key" ]; then
  key="${HOME}/.ssh/sentinel_gpu"
fi
known="${HOME}/.ssh/known_hosts"

exec ssh -N \
  -L "127.0.0.1:${local_port}:127.0.0.1:${remote_port}" \
  -p "$ssh_port" \
  -i "$key" \
  -o BatchMode=yes \
  -o IdentitiesOnly=yes \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile="$known" \
  "${ssh_user}@${host}"
