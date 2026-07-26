#!/usr/bin/env bash
# Start a Cloudflare Quick Tunnel, extract its generated public URL, then run
# DevSpace with the matching OAuth/public-base configuration.
set -euo pipefail

CLOUDFLARED_BIN="${CLOUDFLARED_BIN:-/opt/homebrew/bin/cloudflared}"
DEVSPACE_BIN="${DEVSPACE_BIN:-/opt/homebrew/bin/devspace}"
DEVSPACE_LOCAL_URL="${DEVSPACE_LOCAL_URL:-http://127.0.0.1:7676}"
DEVSPACE_TRUST_PROXY="${DEVSPACE_TRUST_PROXY:-1}"
STARTUP_TIMEOUT="${DEVSPACE_TUNNEL_STARTUP_TIMEOUT:-45}"
RUNTIME_DIR="${DEVSPACE_TUNNEL_RUNTIME_DIR:-/private/tmp/terminal/devspace-cloudflare}"
CLOUDFLARED_LOG="$RUNTIME_DIR/cloudflared.log"

cloudflared_pid=""
devspace_pid=""

require_executable() {
  local path="$1"
  local label="$2"
  if [[ ! -x "$path" ]]; then
    echo "[!] $label executable not found: $path" >&2
    exit 1
  fi
}

stop_child() {
  local pid="${1:-}"
  if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
    return
  fi

  kill -TERM "$pid" 2>/dev/null || true
  local attempt
  for attempt in {1..50}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      wait "$pid" 2>/dev/null || true
      return
    fi
    sleep 0.1
  done

  kill -KILL "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
}

shutdown() {
  local status="${1:-0}"
  trap - EXIT INT TERM HUP
  stop_child "$devspace_pid"
  stop_child "$cloudflared_pid"
  exit "$status"
}

trap 'shutdown $?' EXIT
trap 'shutdown 130' INT
trap 'shutdown 143' TERM HUP

require_executable "$CLOUDFLARED_BIN" "cloudflared"
require_executable "$DEVSPACE_BIN" "devspace"

if ! [[ "$STARTUP_TIMEOUT" =~ ^[0-9]+$ ]] || [[ "$STARTUP_TIMEOUT" -lt 1 ]]; then
  echo "[!] DEVSPACE_TUNNEL_STARTUP_TIMEOUT must be a positive integer." >&2
  exit 2
fi

mkdir -p "$RUNTIME_DIR"
: > "$CLOUDFLARED_LOG"

echo "[+] Starting Cloudflare Quick Tunnel for $DEVSPACE_LOCAL_URL"
"$CLOUDFLARED_BIN" tunnel --url "$DEVSPACE_LOCAL_URL" \
  > >(tee -a "$CLOUDFLARED_LOG") 2>&1 &
cloudflared_pid=$!

public_url=""
deadline=$((SECONDS + STARTUP_TIMEOUT))
while [[ "$SECONDS" -lt "$deadline" ]]; do
  if ! kill -0 "$cloudflared_pid" 2>/dev/null; then
    wait "$cloudflared_pid" 2>/dev/null || true
    echo "[!] cloudflared exited before a public URL was issued." >&2
    exit 1
  fi

  public_url="$(grep -Eo 'https://[A-Za-z0-9-]+\.trycloudflare\.com' "$CLOUDFLARED_LOG" | tail -n 1 || true)"
  if [[ -n "$public_url" ]]; then
    break
  fi
  sleep 0.25
done

if [[ -z "$public_url" ]]; then
  echo "[!] Timed out after ${STARTUP_TIMEOUT}s waiting for the Quick Tunnel URL." >&2
  exit 1
fi

echo "[+] DevSpace public base URL: $public_url"
echo "[+] ChatGPT MCP URL: ${public_url}/mcp"
echo "[!] Quick Tunnel URLs change whenever this managed service is recreated."

DEVSPACE_PUBLIC_BASE_URL="$public_url" \
DEVSPACE_TRUST_PROXY="$DEVSPACE_TRUST_PROXY" \
  "$DEVSPACE_BIN" serve &
devspace_pid=$!

while true; do
  if ! kill -0 "$cloudflared_pid" 2>/dev/null; then
    status=0
    wait "$cloudflared_pid" 2>/dev/null || status=$?
    if [[ "$status" -eq 0 ]]; then
      status=1
    fi
    echo "[!] cloudflared exited; stopping DevSpace." >&2
    exit "$status"
  fi

  if ! kill -0 "$devspace_pid" 2>/dev/null; then
    status=0
    wait "$devspace_pid" 2>/dev/null || status=$?
    echo "[!] DevSpace exited; stopping cloudflared." >&2
    exit "$status"
  fi

  sleep 1
done
