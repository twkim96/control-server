#!/usr/bin/env bash
# Run the project-pinned PM2 CLI against Control Server's isolated PM2_HOME.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NODE_BIN="${CONTROL_PM2_NODE:-/opt/homebrew/bin/node}"
PM2_CLI="${CONTROL_PM2_CLI:-$REPO_ROOT/ops/pm2/node_modules/pm2/bin/pm2}"
PM2_HOME_DIR="${CONTROL_PM2_HOME:-$REPO_ROOT/backend/runtime/pm2}"

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

exec "$NODE_BIN" "$PM2_CLI" "$@"
