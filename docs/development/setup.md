# 개발 환경과 검증

## 환경 준비

macOS/Node/Python 지원 조건은 [설치 안내](../operations/install.md#requirements)에 있습니다.
저장소 루트에서 실행합니다.

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
npm ci --ignore-scripts
npm ci --prefix frontend
npm ci --prefix ops/pm2
npm run build --prefix frontend
cp -n backend/config.example.yml backend/config.yml
cp -n launchd/run.env.example launchd/run.env
```

기존 private 파일이 있으면 덮어쓰지 않고 예시와 비교합니다. `launchd/run.env`의
`CONTROL_PASSWORD`를 충분히 긴 로컬 값으로 지정합니다. 실제 config, run.env,
runtime/log와 서비스 자격증명은 Git에 추가하지 않습니다.

## 로컬 실행

```bash
bash scripts/dev_run.sh
```

기본 주소는 `http://127.0.0.1:9000`이며 Flask가 빌드된 frontend를 제공합니다.
개발 hot reload는 별도 터미널에서 `npm run dev --prefix frontend`로 실행합니다.
Vite 기본 포트는 5173, `/api`는 9000 backend로 proxy합니다.
다른 backend 주소는 frontend 로컬 env의 `VITE_BACKEND_URL`로 지정합니다.
`VITE_API_BASE`는 client의 API base지만 `credentials: same-origin`이므로 cross-origin
인증이 자동 해결된다고 가정하지 않습니다. API 인증은 [API.md](../../API.md#인증)를 따릅니다.

```bash
bash scripts/dev_run.sh   --config /path/to/config.yml   --host 127.0.0.1 --port 9000 --server waitress
```

source 운영 controller나 관리형 설치가 이미 9000을 사용하면 별도 테스트 포트/config/
log/runtime을 지정합니다. `.venv/bin/python backend/app.py --help`에서 해당 인자를 확인합니다.
`CONTROL_PROCESS_BACKEND`는 예시 run.env에 PM2로 설정되어 있습니다.

## 변경에 맞는 검증

```bash
.venv/bin/pytest -q
npm test
npm run typecheck --prefix frontend
npm run lint --prefix frontend
npm run build --prefix frontend
npm run package:inspect
git diff --check
```

위는 전체 개발/릴리스 명령 목록입니다. 작은 문서·스타일 변경은 관련 diff/링크/화면을
확인하고, 구체적 동작 결함에는 영향받는 기존 suite로 회귀를 검증합니다.
`package:inspect`는 frontend build를 포함하며 allowlist와 secret scan을 수행하므로
같은 build를 바로 앞에서 반복하지 않습니다.
문서 배포 경로만 바꾸면 `npm pack --dry-run --ignore-scripts --json`으로 포함 파일을
확인할 수 있습니다. publish나 설치·서비스 재시작은 검증 명령에 포함하지 않습니다.

CI 설정은 `.github/workflows/ci.yml`이며 arm64/x64에서 backend, CLI, frontend 및
패키지 gate를 실행합니다. 성공은 해당 commit의 증거이며 미커밋 변경으로 확대하지 않습니다.

## PM2 통합과 수용 구분

```bash
RUN_PM2_INTEGRATION=1 .venv/bin/pytest -q backend/tests/test_pm2_integration.py
RUN_PM2_ENGINE_INTEGRATION=1 .venv/bin/pytest -q backend/tests/test_pm2_engine_integration.py
```

이 opt-in 검사는 격리된 PM2_HOME과 fixture를 사용합니다. engine 검사는 npm download를
포함합니다. 담당자는 전체 exit 결과와 fixture daemon/자식 정리까지 확인합니다.
일반 suite에서 skip한 실제 integration을 통과했다고 기록하지 않습니다.

기존 운영 서비스·physical Intel·sleep/wake·실제 브라우저 수용은 별도 결과입니다.
packed rollback fixture를 실제 pre-1.5.3 앱 호환 증거로 쓰지 않습니다.
실제 lifecycle 변경은 start/stop/restart/crash/config reload/rollback의 관련 위험을
검증하며 단위 테스트만으로 destructive 동작이 안전하다고 판단하지 않습니다.
