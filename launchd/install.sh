#!/usr/bin/env bash
# 컨트롤 서버를 launchd LaunchAgent로 등록한다.
# 사용:
#   launchd/install.sh load     # 등록 + 즉시 실행
#   launchd/install.sh unload   # 중지 + 등록 해제
#   launchd/install.sh restart  # unload + load
#   launchd/install.sh status   # 현재 상태
#   launchd/install.sh render   # plist만 생성 (load하지 않음)
#
# 이 스크립트는 launchd/com.twkim.server-control.plist 템플릿의
# @@TOKEN@@을 실제 값으로 치환한 뒤 ~/Library/LaunchAgents/에 설치한다.
# 비밀값은 launchd/run.env 또는 현재 셸 환경변수에서 가져온다.
set -euo pipefail
umask 077

REPO_ROOT="${CONTROL_APP_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LABEL="${CONTROL_LAUNCHD_LABEL:-com.twkim.server-control}"
TEMPLATE="$REPO_ROOT/launchd/$LABEL.plist"
if [[ ! -f "$TEMPLATE" ]]; then
    TEMPLATE="$REPO_ROOT/launchd/com.twkim.server-control.plist"
fi
LAUNCHAGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_DST="${CONTROL_PLIST_DST:-$LAUNCHAGENTS_DIR/$LABEL.plist}"
ENV_FILE="${CONTROL_ENV_FILE:-$REPO_ROOT/launchd/run.env}"
PYTHON="${CONTROL_PYTHON:-$REPO_ROOT/.venv/bin/python}"

# 운영 config. 다른 config로 띄우려면 환경변수 SERVER_CONTROL_CONFIG로 override.
CONFIG_FILE="${CONTROL_CONFIG_PATH:-${SERVER_CONTROL_CONFIG:-$REPO_ROOT/backend/config.yml}}"
LOG_DIR="${CONTROL_LOG_DIR:-$REPO_ROOT/backend/logs}"
RUNTIME_DIR="${CONTROL_RUNTIME_DIR:-$REPO_ROOT/backend/runtime}"
FRONTEND_DIST="${CONTROL_FRONTEND_DIST:-$REPO_ROOT/frontend/dist}"

cmd="${1:-status}"

require_python() {
    if [[ ! -x "$PYTHON" ]]; then
        echo "[!] $PYTHON 가 없습니다. 먼저 .venv를 만드세요:"
        echo "    /opt/homebrew/bin/python3 -m venv .venv"
        echo "    .venv/bin/pip install -r backend/requirements.txt"
        exit 1
    fi
}

load_env() {
    # run.env가 있으면 우선 사용. 없으면 호출 환경의 변수를 그대로 사용.
    if [[ -f "$ENV_FILE" ]]; then
        set -a
        # shellcheck disable=SC1090
        source "$ENV_FILE"
        set +a
    fi
}

resolve_layout() {
    PYTHON="${CONTROL_PYTHON:-$REPO_ROOT/.venv/bin/python}"
    CONFIG_FILE="${CONTROL_CONFIG_PATH:-${SERVER_CONTROL_CONFIG:-$REPO_ROOT/backend/config.yml}}"
    LOG_DIR="${CONTROL_LOG_DIR:-$REPO_ROOT/backend/logs}"
    RUNTIME_DIR="${CONTROL_RUNTIME_DIR:-$REPO_ROOT/backend/runtime}"
    FRONTEND_DIST="${CONTROL_FRONTEND_DIST:-$REPO_ROOT/frontend/dist}"
}

require_password() {
    if [[ -z "${CONTROL_PASSWORD:-}" ]]; then
        echo "[!] CONTROL_PASSWORD가 설정되지 않았습니다."
        echo "    launchd/run.env.example을 launchd/run.env로 복사한 뒤 값을 채우거나,"
        echo "    이 스크립트 호출 전에 export CONTROL_PASSWORD=... 하세요."
        exit 1
    fi
}

resolve_node() {
    if [[ -n "${CONTROL_PM2_NODE:-}" ]]; then
        if [[ ! -x "$CONTROL_PM2_NODE" ]]; then
            echo "[!] CONTROL_PM2_NODE가 실행 파일이 아닙니다: $CONTROL_PM2_NODE"
            exit 1
        fi
        printf '%s\n' "$CONTROL_PM2_NODE"
        return
    fi
    local node_path
    node_path="$(command -v node 2>/dev/null || true)"
    if [[ -z "$node_path" ]]; then
        for candidate in /opt/homebrew/bin/node /usr/local/bin/node; do
            if [[ -x "$candidate" ]]; then
                node_path="$candidate"
                break
            fi
        done
    fi
    if [[ -z "$node_path" ]]; then
        echo "[!] Node.js 실행 파일을 찾지 못했습니다." >&2
        exit 1
    fi
    printf '%s\n' "$node_path"
}

render_plist() {
    local node_bin
    local tmp_plist="${PLIST_DST}.tmp.$$"
    node_bin="$(resolve_node)"
    mkdir -p "$LOG_DIR" "$RUNTIME_DIR" "$(dirname "$CONFIG_FILE")"
    # @@토큰@@을 실제 값으로 치환. PASSWORD 안의 '/' 같은 문자도 안전하게 처리하기 위해
    # python을 사용한다.
    rm -f "$tmp_plist"
    if ! "$PYTHON" - "$TEMPLATE" "$LABEL" "$REPO_ROOT" "$PYTHON" "$CONFIG_FILE" "$LOG_DIR" "$RUNTIME_DIR" "$FRONTEND_DIST" "$node_bin" <<'PY' > "$tmp_plist"
import os, sys, html
template_path, label, repo, py, cfg, log_dir, runtime_dir, frontend_dist, pm2_node = sys.argv[1:10]
pw = os.environ["CONTROL_PASSWORD"]
secret = os.environ.get("CONTROL_SECRET_KEY", "")
pm2_cli = os.environ.get(
    "CONTROL_PM2_CLI", os.path.join(repo, "ops", "pm2", "node_modules", "pm2", "bin", "pm2")
)
pm2_home = os.environ.get("CONTROL_PM2_HOME", os.path.join(runtime_dir, "pm2"))
process_backend = os.environ.get("CONTROL_PROCESS_BACKEND", "pm2")
with open(template_path, "r", encoding="utf-8") as f:
    text = f.read()
escape = lambda value: html.escape(value, quote=False)
text = (text
    .replace("@@LABEL@@", html.escape(label, quote=False))
    .replace("@@REPO_ROOT@@", escape(repo))
    .replace("@@PYTHON@@", escape(py))
    .replace("@@CONFIG@@", escape(cfg))
    .replace("@@LOG_DIR@@", escape(log_dir))
    .replace("@@RUNTIME_DIR@@", escape(runtime_dir))
    .replace("@@FRONTEND_DIST@@", escape(frontend_dist))
    .replace("@@CONTROL_PASSWORD@@", html.escape(pw, quote=False))
    .replace("@@CONTROL_SECRET@@", html.escape(secret, quote=False))
    .replace("@@CONTROL_PM2_NODE@@", html.escape(pm2_node, quote=False))
    .replace("@@CONTROL_PM2_CLI@@", html.escape(pm2_cli, quote=False))
    .replace("@@CONTROL_PM2_HOME@@", html.escape(pm2_home, quote=False))
    .replace("@@CONTROL_PROCESS_BACKEND@@", html.escape(process_backend, quote=False)))
sys.stdout.write(text)
PY
    then
        rm -f "$tmp_plist"
        return 1
    fi
    if ! /usr/bin/plutil -lint "$tmp_plist" >/dev/null; then
        rm -f "$tmp_plist"
        return 1
    fi
    chmod 600 "$tmp_plist"
    mv -f "$tmp_plist" "$PLIST_DST"
}

case "$cmd" in
    render)
        load_env
        resolve_layout
        require_python
        require_password
        mkdir -p "$(dirname "$PLIST_DST")"
        render_plist
        echo "[+] rendered: $PLIST_DST"
        ;;
    load)
        load_env
        resolve_layout
        require_python
        require_password
        mkdir -p "$LAUNCHAGENTS_DIR"
        render_plist
        launchctl unload "$PLIST_DST" 2>/dev/null || true
        launchctl load -w "$PLIST_DST"
        echo "[+] loaded: $PLIST_DST"
        echo "    config: $CONFIG_FILE"
        echo "    runtime: $RUNTIME_DIR"
        echo "    logs: $LOG_DIR"
        ;;
    unload)
        if [[ -f "$PLIST_DST" ]]; then
            launchctl unload -w "$PLIST_DST" 2>/dev/null || true
            rm -f "$PLIST_DST"
            echo "[+] unloaded"
        else
            echo "[ ] (already unloaded)"
        fi
        ;;
    restart)
        "$0" unload || true
        sleep 1
        "$0" load
        ;;
    status)
        # SIGPIPE로 인한 launchctl exit 비0을 무시하기 위해 grep 결과를 임시로 받는다.
        if list_output=$(launchctl list 2>/dev/null) && \
           printf '%s\n' "$list_output" | grep -q "$LABEL"; then
            echo "[+] $LABEL is loaded"
            printf '%s\n' "$list_output" | grep "$LABEL" || true
            echo "    plist: $PLIST_DST"
        else
            echo "[ ] $LABEL is NOT loaded"
        fi
        ;;
    *)
        echo "usage: $0 {render|load|unload|restart|status}"
        exit 1
        ;;
esac
