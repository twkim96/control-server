#!/usr/bin/env bash
# Run the project-pinned PM2 CLI against Control Server's isolated PM2_HOME.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NODE_BIN="${CONTROL_PM2_NODE:-/opt/homebrew/bin/node}"
PM2_CLI="${CONTROL_PM2_CLI:-$REPO_ROOT/ops/pm2/node_modules/pm2/bin/pm2}"
PM2_HOME_DIR="${CONTROL_PM2_HOME:-$REPO_ROOT/backend/runtime/pm2}"
ENGINE_ROOT="$(dirname "$PM2_HOME_DIR")/pm2-engine"
TASKPOLICY_BIN="${CONTROL_PM2_TASKPOLICY:-}"

# macOS sockaddr_un.sun_path is 104 bytes. PM2 appends interactor.sock (15
# characters), so reject an unsafe home before the CLI loops on EINVAL.
if (( ${#PM2_HOME_DIR} + 16 >= 104 )); then
  echo "[!] CONTROL_PM2_HOME is too long for macOS PM2 sockets: $PM2_HOME_DIR" >&2
  exit 1
fi

if [[ ! -x "$NODE_BIN" ]]; then
  echo "[!] PM2 Node runtime is not executable: $NODE_BIN" >&2
  exit 1
fi
umask 077
mkdir -p "$PM2_HOME_DIR"
chmod 700 "$PM2_HOME_DIR"
mkdir -p "$ENGINE_ROOT"
chmod 700 "$ENGINE_ROOT"

# A kernel lock excludes all ordinary clients during engine cutover. The updater
# alone may bypass it with the private token written while holding the same lock.
OWNER_TOKEN=""
if [[ -f "$ENGINE_ROOT/maintenance-owner" ]]; then
  IFS= read -r OWNER_TOKEN < "$ENGINE_ROOT/maintenance-owner" || true
fi
if [[ -z "$OWNER_TOKEN" || "${CONTROL_PM2_ENGINE_TOKEN:-}" != "$OWNER_TOKEN" ]]; then
  exec 8>>"$ENGINE_ROOT/maintenance.lock"
  # Ordinary status/action calls queue behind one another. Only an active engine
  # updater needs immediate rejection; otherwise a routine poll could block stop.
  MAINTENANCE_WAIT=10
  if [[ -n "$OWNER_TOKEN" ]]; then MAINTENANCE_WAIT=0; fi
  if ! /usr/bin/lockf -s -t "$MAINTENANCE_WAIT" 8; then
    echo "[!] PM2 engine maintenance is in progress" >&2
    exit 75
  fi
  if [[ -f "$ENGINE_ROOT/recovery.json" ]]; then
    echo "[!] PM2 engine recovery is required" >&2
    exit 75
  fi
fi
if [[ -n "${CONTROL_PM2_ENGINE_CLI:-}" ]]; then
  PM2_CLI="$CONTROL_PM2_ENGINE_CLI"
elif [[ -f "$ENGINE_ROOT/current/node_modules/pm2/bin/pm2" ]]; then
  PM2_CLI="$ENGINE_ROOT/current/node_modules/pm2/bin/pm2"
fi
if [[ ! -f "$PM2_CLI" ]]; then
  echo "[!] PM2 is not installed: $PM2_CLI" >&2
  exit 1
fi

export PM2_HOME="$PM2_HOME_DIR"
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# Serialize CLI calls using a kernel lock, released even when the CLI is killed.
PM2_CLI_LOCK="$PM2_HOME_DIR/server-control-cli.lock"
exec 9>>"$PM2_CLI_LOCK"
if ! /usr/bin/lockf -s -t 10 9; then
  echo "[!] PM2 CLI lock timed out: $PM2_CLI_LOCK" >&2
  exit 75
fi

if [[ "${1:-}" == "__engine_snapshot" ]]; then
  exec "$NODE_BIN" "$REPO_ROOT/lib/pm2-probe.mjs" "$PM2_CLI"
fi

# Older installs ran the Control Server as ProcessType=Background. Keep the PM2
# CLI override so an installed stale plist or other background caller cannot
# turn a sub-second jlist into a 6-20 second request. This affects only the
# short-lived CLI process.
if [[ -z "$TASKPOLICY_BIN" && "$OSTYPE" == darwin* ]]; then
  TASKPOLICY_BIN="/usr/sbin/taskpolicy"
fi
if [[ -n "$TASKPOLICY_BIN" && -x "$TASKPOLICY_BIN" ]]; then
  exec "$TASKPOLICY_BIN" -a "$NODE_BIN" "$PM2_CLI" "$@"
fi

exec "$NODE_BIN" "$PM2_CLI" "$@"
