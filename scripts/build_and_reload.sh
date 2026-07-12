#!/usr/bin/env bash
# 프론트 빌드 + (선택) launchd로 띄운 백엔드 restart.
# 사용:
#   bash scripts/build_and_reload.sh         # 프론트만 빌드
#   bash scripts/build_and_reload.sh --all   # 백엔드도 restart
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> frontend build"
cd frontend
npm run build
cd "$REPO_ROOT"

if [[ "${1:-}" == "--all" ]]; then
    echo "==> launchd restart"
    bash launchd/install.sh restart
fi

echo "==> done. 브라우저 ⌘⇧R 로 새로고침하세요."
