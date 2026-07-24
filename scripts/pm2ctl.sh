#!/usr/bin/env bash
# Run the project-pinned PM2 CLI against Control Server's isolated PM2_HOME.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NODE_BIN="${CONTROL_PM2_NODE:-/opt/homebrew/bin/node}"
PM2_CLI="${CONTROL_PM2_CLI:-$REPO_ROOT/ops/pm2/node_modules/pm2/bin/pm2}"
PM2_HOME_DIR="${CONTROL_PM2_HOME:-$REPO_ROOT/backend/runtime/pm2}"

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

exec "$NODE_BIN" "$PM2_CLI" "$@"
