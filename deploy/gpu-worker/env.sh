#!/usr/bin/env bash
# Read one KEY=value from .env without executing the file.

read_env() {
  local key="$1"
  local default="${2:-}"
  local line=""
  if [ -f .env ]; then
    line="$(grep -E "^${key}=" .env | tail -n 1 || true)"
  fi
  if [ -z "$line" ]; then
    printf '%s' "$default"
    return
  fi
  local value="${line#*=}"
  value="${value%\"}"
  value="${value#\"}"
  value="${value%\'}"
  value="${value#\'}"
  printf '%s' "$value"
}
