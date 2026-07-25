#!/usr/bin/env bash
# Run the project-pinned PM2 CLI against Control Server's isolated PM2_HOME.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NODE_BIN="${CONTROL_PM2_NODE:-/opt/homebrew/bin/node}"
PM2_CLI="${CONTROL_PM2_CLI:-$REPO_ROOT/ops/pm2/node_modules/pm2/bin/pm2}"
PM2_HOME_DIR="${CONTROL_PM2_HOME:-$REPO_ROOT/backend/runtime/pm2}"
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
if [[ ! -f "$PM2_CLI" ]]; then
  echo "[!] PM2 is not installed: $PM2_CLI" >&2
  echo "    Run: /opt/homebrew/bin/npm ci --prefix $REPO_ROOT/ops/pm2" >&2
  exit 1
fi

umask 077
mkdir -p "$PM2_HOME_DIR"
chmod 700 "$PM2_HOME_DIR"

export PM2_HOME="$PM2_HOME_DIR"
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# PM2 can create a second God daemon when independent CLI clients race while
# the shared pid/socket state is slow. shlock records this wrapper PID; exec
# keeps that PID alive for the Node CLI, and a killed/exited CLI is reclaimed
# atomically by the next invocation. Bound the wait below Pm2Manager's timeout.
PM2_CLI_LOCK="$PM2_HOME_DIR/server-control-cli.lock"
lock_acquired=false
for _ in {1..100}; do
  if /usr/bin/shlock -f "$PM2_CLI_LOCK" -p $$; then
    lock_acquired=true
    break
  fi
  sleep 0.1
done
if [[ "$lock_acquired" != true ]]; then
  echo "[!] PM2 CLI lock timed out: $PM2_CLI_LOCK" >&2
  exit 75
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
