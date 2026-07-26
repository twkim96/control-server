#!/usr/bin/env bash
# Run DevSpace and the account-assigned ngrok endpoint as one managed process.
# DevSpace is made healthy first so ngrok never forwards to an unopened port.
# Stopping this wrapper stops both child processes.
set -euo pipefail

export PATH="${DEVSPACE_PATH:-/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin}"

NGROK_BIN="${NGROK_BIN:-/opt/homebrew/bin/ngrok}"
DEVSPACE_BIN="${DEVSPACE_BIN:-/opt/homebrew/bin/devspace}"
DEVSPACE_LOCAL_URL="${DEVSPACE_LOCAL_URL:-http://127.0.0.1:7676}"
DEVSPACE_HEALTH_URL="${DEVSPACE_HEALTH_URL:-${DEVSPACE_LOCAL_URL%/}/.well-known/oauth-authorization-server}"
DEVSPACE_PUBLIC_BASE_URL="${DEVSPACE_PUBLIC_BASE_URL:-https://scurvy-paddling-causing.ngrok-free.dev}"
DEVSPACE_TRUST_PROXY="${DEVSPACE_TRUST_PROXY:-1}"
STARTUP_TIMEOUT="${DEVSPACE_TUNNEL_STARTUP_TIMEOUT:-30}"
NGROK_API_URL="${NGROK_API_URL:-http://127.0.0.1:4040/api/tunnels}"

ngrok_pid=""
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
  # Remove the public route before stopping the local MCP server.
  stop_child "$ngrok_pid"
  stop_child "$devspace_pid"
  exit "$status"
}

trap 'shutdown $?' EXIT
trap 'shutdown 130' INT
trap 'shutdown 143' TERM HUP

require_executable "$NGROK_BIN" "ngrok"
require_executable "$DEVSPACE_BIN" "devspace"
require_executable "/usr/bin/curl" "curl"

if ! [[ "$STARTUP_TIMEOUT" =~ ^[0-9]+$ ]] || [[ "$STARTUP_TIMEOUT" -lt 1 ]]; then
  echo "[!] DEVSPACE_TUNNEL_STARTUP_TIMEOUT must be a positive integer." >&2
  exit 2
fi

case "$DEVSPACE_PUBLIC_BASE_URL" in
  https://*) ;;
  *)
    echo "[!] DEVSPACE_PUBLIC_BASE_URL must start with https://" >&2
    exit 2
    ;;
esac

DEVSPACE_PUBLIC_BASE_URL="${DEVSPACE_PUBLIC_BASE_URL%/}"

echo "[+] Starting DevSpace on $DEVSPACE_LOCAL_URL"
DEVSPACE_PUBLIC_BASE_URL="$DEVSPACE_PUBLIC_BASE_URL" \
DEVSPACE_TRUST_PROXY="$DEVSPACE_TRUST_PROXY" \
  "$DEVSPACE_BIN" serve &
devspace_pid=$!

ready=false
deadline=$((SECONDS + STARTUP_TIMEOUT))
while [[ "$SECONDS" -lt "$deadline" ]]; do
  if ! kill -0 "$devspace_pid" 2>/dev/null; then
    status=0
    wait "$devspace_pid" 2>/dev/null || status=$?
    echo "[!] DevSpace exited before becoming healthy (status $status)." >&2
    exit "${status:-1}"
  fi

  if /usr/bin/curl -fsS --max-time 2 -o /dev/null "$DEVSPACE_HEALTH_URL" 2>/dev/null; then
    ready=true
    break
  fi
  sleep 0.25
done

if [[ "$ready" != true ]]; then
  echo "[!] Timed out after ${STARTUP_TIMEOUT}s waiting for DevSpace health: $DEVSPACE_HEALTH_URL" >&2
  exit 1
fi

echo "[+] DevSpace health check passed: $DEVSPACE_HEALTH_URL"
echo "[+] Starting ngrok: $DEVSPACE_PUBLIC_BASE_URL -> $DEVSPACE_LOCAL_URL"
"$NGROK_BIN" http "$DEVSPACE_LOCAL_URL" \
  --url "$DEVSPACE_PUBLIC_BASE_URL" \
  --log stdout \
  --log-format logfmt &
ngrok_pid=$!

ready=false
deadline=$((SECONDS + STARTUP_TIMEOUT))
while [[ "$SECONDS" -lt "$deadline" ]]; do
  if ! kill -0 "$ngrok_pid" 2>/dev/null; then
    status=0
    wait "$ngrok_pid" 2>/dev/null || status=$?
    echo "[!] ngrok exited before the endpoint became ready (status $status)." >&2
    echo "[!] Stop any manually started ngrok process, then use the Control Server button again." >&2
    exit "${status:-1}"
  fi

  tunnels_json="$(/usr/bin/curl -fsS --max-time 2 "$NGROK_API_URL" 2>/dev/null || true)"
  if [[ "$tunnels_json" == *"$DEVSPACE_PUBLIC_BASE_URL"* ]]; then
    ready=true
    break
  fi
  sleep 0.25
done

if [[ "$ready" != true ]]; then
  echo "[!] Timed out after ${STARTUP_TIMEOUT}s waiting for ngrok endpoint readiness." >&2
  exit 1
fi

echo "[+] DevSpace public base URL: $DEVSPACE_PUBLIC_BASE_URL"
echo "[+] ChatGPT MCP URL: ${DEVSPACE_PUBLIC_BASE_URL}/mcp"

while true; do
  if ! kill -0 "$devspace_pid" 2>/dev/null; then
    status=0
    wait "$devspace_pid" 2>/dev/null || status=$?
    if [[ "$status" -eq 0 ]]; then
      status=1
    fi
    echo "[!] DevSpace exited; stopping ngrok." >&2
    exit "$status"
  fi

  if ! kill -0 "$ngrok_pid" 2>/dev/null; then
    status=0
    wait "$ngrok_pid" 2>/dev/null || status=$?
    if [[ "$status" -eq 0 ]]; then
      status=1
    fi
    echo "[!] ngrok exited; stopping DevSpace." >&2
    exit "$status"
  fi

  sleep 1
done
