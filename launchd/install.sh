#!/usr/bin/env bash
# 컨트롤 서버를 launchd LaunchAgent로 등록한다.
# 사용:
#   launchd/install.sh load     # 등록 + 즉시 실행
#   launchd/install.sh unload   # 중지 + 등록 해제
#   launchd/install.sh restart  # unload + load
#   launchd/install.sh status   # 현재 상태
#
# 이 스크립트는 launchd/com.twkim.server-control.plist 템플릿의
# @@TOKEN@@을 실제 값으로 치환한 뒤 ~/Library/LaunchAgents/에 설치한다.
# 비밀값은 launchd/run.env 또는 현재 셸 환경변수에서 가져온다.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.twkim.server-control"
TEMPLATE="$REPO_ROOT/launchd/$LABEL.plist"
LAUNCHAGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_DST="$LAUNCHAGENTS_DIR/$LABEL.plist"
ENV_FILE="$REPO_ROOT/launchd/run.env"
PYTHON="$REPO_ROOT/.venv/bin/python"

# 운영 config. 다른 config로 띄우려면 환경변수 SERVER_CONTROL_CONFIG로 override.
CONFIG_FILE="${SERVER_CONTROL_CONFIG:-$REPO_ROOT/backend/config.yml}"

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

require_password() {
    if [[ -z "${CONTROL_PASSWORD:-}" ]]; then
        echo "[!] CONTROL_PASSWORD가 설정되지 않았습니다."
        echo "    launchd/run.env.example을 launchd/run.env로 복사한 뒤 값을 채우거나,"
        echo "    이 스크립트 호출 전에 export CONTROL_PASSWORD=... 하세요."
        exit 1
    fi
}

render_plist() {
    local secret="${CONTROL_SECRET_KEY:-}"
    local pw="$CONTROL_PASSWORD"
    # @@토큰@@을 실제 값으로 치환. PASSWORD 안의 '/' 같은 문자도 안전하게 처리하기 위해
    # python을 사용한다.
    "$PYTHON" - "$TEMPLATE" "$REPO_ROOT" "$PYTHON" "$CONFIG_FILE" "$pw" "$secret" <<'PY' > "$PLIST_DST"
import os, sys, html
template_path, repo, py, cfg, pw, secret = sys.argv[1:7]
novelpia_email = os.environ.get("FILE_CHECK_NOVELPIA_EMAIL", "")
novelpia_password = os.environ.get("FILE_CHECK_NOVELPIA_PASSWORD", "")
google_credentials = os.environ.get("FILE_CHECK_GOOGLE_CREDENTIALS", "")
google_spreadsheet_id = os.environ.get("FILE_CHECK_GOOGLE_SPREADSHEET_ID", "")
with open(template_path, "r", encoding="utf-8") as f:
    text = f.read()
text = (text
    .replace("@@REPO_ROOT@@", repo)
    .replace("@@PYTHON@@", py)
    .replace("@@CONFIG@@", cfg)
    .replace("@@CONTROL_PASSWORD@@", html.escape(pw, quote=False))
    .replace("@@CONTROL_SECRET@@", html.escape(secret, quote=False))
    .replace("@@NOVELPIA_EMAIL@@", html.escape(novelpia_email, quote=False))
    .replace("@@NOVELPIA_PASSWORD@@", html.escape(novelpia_password, quote=False))
    .replace("@@GOOGLE_CREDENTIALS@@", html.escape(google_credentials, quote=False))
    .replace("@@GOOGLE_SPREADSHEET_ID@@", html.escape(google_spreadsheet_id, quote=False)))
sys.stdout.write(text)
PY
    chmod 600 "$PLIST_DST"
}

case "$cmd" in
    load)
        require_python
        load_env
        require_password
        mkdir -p "$LAUNCHAGENTS_DIR"
        render_plist
        launchctl unload "$PLIST_DST" 2>/dev/null || true
        launchctl load -w "$PLIST_DST"
        echo "[+] loaded: $PLIST_DST"
        echo "    config: $CONFIG_FILE"
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
        echo "usage: $0 {load|unload|restart|status}"
        exit 1
        ;;
esac
