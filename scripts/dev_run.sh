#!/usr/bin/env bash
# 백엔드 개발 실행 스크립트.
# CONTROL_PASSWORD는 환경변수 또는 ignore된 launchd/run.env에서 읽는다.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

if [[ -z "${CONTROL_PASSWORD:-}" && -f "$SCRIPT_DIR/launchd/run.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/launchd/run.env"
  set +a
fi

if [[ -z "${CONTROL_PASSWORD:-}" ]]; then
  echo "[!] CONTROL_PASSWORD가 설정되지 않았습니다." >&2
  echo "    launchd/run.env.example을 launchd/run.env로 복사해 값을 채우거나," >&2
  echo "    실행 전 CONTROL_PASSWORD를 export하세요." >&2
  exit 1
fi

export CONTROL_PASSWORD
export PYTHONUNBUFFERED=1

exec .venv/bin/python backend/app.py "$@"
