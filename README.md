# Control Server

로컬에서 실행하는 여러 서비스와 일회성 명령을 한 화면에서 관리하는 macOS용 웹
컨트롤러입니다. 프로세스 시작·중지·재시작, 상태 확인, 로그 스트리밍, 리소스 모니터링,
서비스 설정 편집과 작업 실행을 지원합니다.

> 이 프로젝트는 localhost, LAN 또는 Tailscale 같은 신뢰된 사설망에서 사용하는 것을
> 전제로 합니다. 인터넷에 직접 노출하는 용도로 설계되지 않았습니다.

## 주요 기능

- 등록된 서비스의 시작, 중지, 재시작 및 상태 확인
- HTTP/TCP 헬스체크와 포트 점유 감지
- stdout/stderr 로그 tail 및 SSE 실시간 스트리밍
- CPU, 메모리, 자식 프로세스 리소스 표시
- 웹 UI에서 서비스와 일회성 Action Group 등록·수정·정렬
- 외부에서 이미 실행 중인 프로세스 감지와 중복 실행 방지
- Python 인터프리터 자동 탐색
- 세션 쿠키 로그인과 CSRF 보호
- launchd로 Control Server 자동 실행, 격리 PM2로 등록 서비스 관리

## 구성

- Backend: Python, Flask, Waitress, psutil, ruamel.yaml
- Frontend: React, TypeScript, Vite
- Runtime: macOS launchd (Control Server), isolated PM2 (managed services, v1.4.1)
- Configuration: YAML

프로젝트의 HTTP API는 [API.md](./API.md)를 참고하세요.

## 요구 사항

- macOS
- Python 3.10 이상
- Node.js 22 이상과 npm

Tailscale HTTPS 인증서 기능을 사용할 때는 Tailscale 앱과 CLI가 추가로 필요합니다.

## 빠른 시작

```bash
git clone https://github.com/twkim96/control-server.git
cd control-server

# Python 환경
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt

# 프런트엔드 설치 및 빌드
cd frontend
npm ci
npm run build
cd ..

# 장기 실행 서비스 관리용 pinned PM2
/opt/homebrew/bin/npm ci --prefix ops/pm2

# 로컬 설정과 비밀번호 파일
cp backend/config.example.yml backend/config.yml
cp launchd/run.env.example launchd/run.env
```

`launchd/run.env`의 `CONTROL_PASSWORD`를 충분히 긴 값으로 바꾼 후 실행합니다.

```bash
bash scripts/dev_run.sh
```

기본 주소는 [http://127.0.0.1:9000](http://127.0.0.1:9000)입니다.

다음 파일은 머신별 경로와 비밀값을 포함할 수 있어 Git에서 추적하지 않습니다.

- `backend/config.yml`
- `launchd/run.env`
- `backend/runtime/`
- `backend/logs/`

## 개발 모드

빌드된 프런트엔드는 Flask가 직접 제공합니다. 프런트엔드 hot reload가 필요하면 백엔드와
Vite를 각각 실행합니다.

```bash
# 터미널 1
bash scripts/dev_run.sh

# 터미널 2
cd frontend
npm run dev
```

Vite 개발 서버는 기본적으로 [http://localhost:5173](http://localhost:5173)에서 열리고
`/api/*` 요청을 백엔드로 전달합니다.

다른 설정 파일이나 주소를 사용하려면 백엔드 인자를 전달할 수 있습니다.

```bash
bash scripts/dev_run.sh \
  --config /path/to/config.yml \
  --host 127.0.0.1 \
  --port 9000 \
  --server waitress
```

## launchd 운영

```bash
bash launchd/install.sh load
bash launchd/install.sh status
bash launchd/install.sh restart
bash launchd/install.sh unload
```

기본 설정은 `backend/config.yml`입니다. 다른 파일을 사용하려면 설치 명령에
`SERVER_CONTROL_CONFIG`를 전달합니다.

```bash
SERVER_CONTROL_CONFIG=/path/to/config.yml bash launchd/install.sh restart
```

LaunchAgent는 `~/Library/LaunchAgents/com.twkim.server-control.plist`에 생성되며
사용자만 읽을 수 있도록 권한을 설정합니다.

## 설정

`backend/config.example.yml`을 복사한 `backend/config.yml`이 기본 운영 설정입니다.
웹 UI에서 변경한 내용도 이 파일에 저장됩니다.

```yaml
controller:
  host: "127.0.0.1"
  port: 9000
  editable_config: true
  allowed_path_roots:
    - "/path/to/projects"
  auth:
    type: "password"
    password_env: "CONTROL_PASSWORD"

services:
  - id: "example"
    name: "Example Service"
    description: "Example HTTP service"
    cwd: "/path/to/projects/example"
    entry_file: "app.py"
    command: ["python", "-u", "app.py"]
    env:
      PORT: "8080"
    port: 8080
    port_env_name: "PORT"
    open_url: "http://127.0.0.1:8080"
    health:
      enabled: true
      type: "http"
      url: "http://127.0.0.1:8080/health"
      timeout_seconds: 2
      verify_ssl: true
    log:
      enabled: true
      tail_lines: 200
      max_bytes: 5242880
      keep: 3
    lifecycle:
      mode: "manual"
      autostart: false
      stop_visibility: "primary"
      restart_visibility: "primary"
      unmanaged_policy: "status_only"
    actions:
      - id: "start"
        label: "Start"
        type: "process_start"
        enabled: true
      - id: "stop"
        label: "Stop"
        type: "process_stop"
        enabled: true
        strategy:
          signal: "SIGINT"
          timeout_seconds: 5
          confirm_required: false
          fallback: ["SIGTERM", "SIGKILL"]
      - id: "restart"
        label: "Restart"
        type: "process_restart"
        enabled: true

actions: []
```

`controller.allowed_path_roots`는 웹 UI의 경로 탐색과 Action 실행 범위를 제한합니다.
서비스 및 Python Action의 작업 디렉터리는 반드시 허용 루트 안에 두세요.

## 관리 대상 서비스의 권장 조건

- 개발 reloader 없이 단일 부모 프로세스로 실행
- `SIGINT`와 `SIGTERM`을 처리하고 자식 프로세스까지 종료
- stdout/stderr를 line-buffered로 출력
- 포트와 host를 환경변수로 받을 수 있도록 구성
- 가능하면 2xx를 반환하는 HTTP health endpoint 제공

Control Server는 시작 전 포트 점유를 확인하며, 등록 서비스는 전용
`backend/runtime/pm2` 아래의 PM2 daemon에서 `server-control--<service_id>` 이름으로
관리합니다. 사용자 기본 `~/.pm2`와는 별개입니다.

PM2 운영 모드의 `process_stop` primary signal은 `SIGINT`여야 하며, 설정된 `SIGTERM`
fallback 뒤 마지막 `SIGKILL`은 PM2가 수행합니다. 로그의 `max_bytes`/`keep` 회전은 PM2가
stopped를 확정한 뒤 또는 stopped 서비스를 다시 시작하기 직전에 적용됩니다. 실행 중인
서비스 정의를 config에서 제거하면 orphan 방지를 위해 reload가 409로 거부되므로 먼저
서비스를 중지하세요.

### 외부 프로세스와 PM2 소유권

PM2 운영 모드에서는 같은 포트에서 외부 프로세스가 발견돼도 자동 입양하지 않습니다.
서비스 상세에는 `running_external`과 함께 실제 안전 진단 사유가 표시됩니다. 명령/cwd/
health 검증을 통과한 외부 프로세스만 `pm2_exclusive`가 되고, 불일치는
`cmdline_mismatch`, `cwd_mismatch`, `health_failed` 등 원래 사유를 유지합니다. 중복 실행을
피하려면 외부 프로세스를 확인해 정상 종료한 뒤 Control Server에서 서비스를 시작하세요.

`kill_external`은 기존 안전 검증을 통과한 외부 프로세스에만 사용할 수 있습니다.
명령/cwd/health가 일치하지 않으면 종료를 거부합니다.

### PM2 운영 명령

항상 저장소 wrapper를 사용하세요. bare `pm2`는 사용자 기본 daemon을 가리킬 수 있습니다.

```bash
scripts/pm2ctl.sh jlist
scripts/pm2ctl.sh status
scripts/pm2ctl.sh logs server-control--SERVICE_ID
```

운영 복구와 pre-PM2 rollback 절차는
[PM2_RECOVERY.md](./PM2_RECOVERY.md)에 있습니다.

## 일회성 작업

최상위 `actions`에는 Services 탭에서 실행할 Action Group을 등록합니다.

- `kind: "python"`: 허용된 cwd에서 Python 명령 실행
- `kind: "argv"`: 셸을 거치지 않고 argv 형태로 명령 실행
- 실행별 로그 보관, 취소 및 SSE 스트리밍
- 사전에 등록한 외부 로그 파일 tail 및 스트리밍

위험한 셸 토큰과 허용 루트 밖 경로는 백엔드에서도 다시 검사합니다.

## Tailscale HTTPS

`scripts/with_tailscale_https.sh`는 Tailscale 인증서를 준비한 후 지정한 명령을
실행하는 wrapper입니다. 실제 tailnet 도메인은 저장소에 넣지 말고 서비스 환경변수로
설정합니다.

```yaml
command:
  - "/bin/bash"
  - "/path/to/control-server/scripts/with_tailscale_https.sh"
  - "--"
  - "python"
  - "-u"
  - "app.py"
env:
  TAILSCALE_HTTPS_DOMAIN: "device.example-tailnet.ts.net"
```

wrapper는 `TAILSCALE_HTTPS_DOMAIN` 또는 `HTTPS_DOMAIN`이 없으면 실행을 거부합니다.
인증서와 개인키 파일은 Git에 추가하지 마세요.

## 보안 모델

- `CONTROL_PASSWORD`: 필수 로그인 비밀번호
- `CONTROL_SECRET_KEY`: 선택적인 세션 서명 키
- 세션: HttpOnly, SameSite=Lax 쿠키
- 변경 요청: `X-CSRF-Token` 검증
- 비밀키 미지정 시 `backend/runtime/.secret_key`에 자동 생성

`CONTROL_PASSWORD`가 없으면 보호된 API는 `503 password_not_configured`로 거부됩니다.
기본 세션 쿠키는 `Secure` 속성을 강제하지 않으므로, 공용 인터넷에 직접 노출하지
마세요. 원격 접근은 Tailscale이나 신뢰된 사설망을 권장합니다.

## 프로젝트 구조

```text
backend/
  app.py                 Flask/Waitress 진입점
  config.example.yml     공개 설정 예시
  config_loader.py       설정 파싱과 검증
  config_writer.py       원자적 설정 저장
  pm2_manager.py         격리 PM2 서비스 수명주기
  process_manager.py     외부 프로세스 안전 진단과 rollback 호환
  routes/                HTTP API
  tests/                 백엔드 테스트
frontend/
  src/api/               API 클라이언트
  src/features/          화면별 React 기능
launchd/
  install.sh             Control Server LaunchAgent 관리
  run.env.example        비밀값 템플릿
ops/pm2/
  package.json           고정 PM2 의존성
scripts/
  dev_run.sh             개발 실행
  pm2ctl.sh              전용 PM2_HOME wrapper
  with_tailscale_https.sh
```

## 테스트와 빌드

```bash
# 백엔드
.venv/bin/pytest -q

# 프런트엔드
cd frontend
npm run typecheck
npm run lint
npm run build
```

## 문제 해결

### 서버 시작 실패

`backend/logs/<service_id>.log`에서 오류를 확인하세요. 흔한 원인은 포트 점유,
존재하지 않는 cwd/entry file, 누락된 의존성입니다.

### launchd 시작 실패

```bash
cat backend/logs/_launchd.err
cat backend/logs/_launchd.out
bash launchd/install.sh status
```

`CONTROL_PASSWORD`, `frontend/dist`, Python 가상환경과 설정 파일을 확인하세요.

### 외부 프로세스로 표시됨

PM2 모드에서는 자동 입양하지 않는 것이 정상입니다. 서비스 상세의
`adopt_diagnostics`에서 포트 점유 PID와 `pm2_exclusive`,
`cmdline_mismatch`, `cwd_mismatch`, `health_failed` 등의 사유를 확인하세요.

### `PM2 command timed out: jlist`

Servers 목록은 직전 정상 PM2 snapshot이 있으면 그대로 표시하면서 background에서 상태를
다시 읽는다. PM2 갱신이 2초 이상 지연되거나 실패하면 화면에 “마지막 정상 상태 표시 중”
경고가 나타난다. 이때 조회 화면은 유지되지만 시작·중지·재시작은 PM2 응답을 확인하지
못하면 실패한다.

Control Server 시작 시 autostart 확인도 background에서 실행하므로 느린 `jlist`가 9000
리스너 시작을 지연시키지 않는다. 최초 snapshot이 아직 없으면 Servers 탭은 잠시
`unknown` 상태를 표시하고 background 조회가 끝난 다음 정상 상태로 갱신된다.

`backend/logs/_launchd.err`에서 `PM2 command slow`, `timed out`, `returncode`,
`stderr_bytes`를 확인한다. 보안을 위해 PM2 stdout/stderr 원문과 서비스 환경변수는 로그에
기록하지 않는다. 전용 daemon을 확인할 때는 bare `pm2` 대신 다음 wrapper만 사용한다.

Control Server LaunchAgent는 사용자 요청을 직접 처리하므로 `ProcessType=Standard`를
사용한다. 무제한 `Interactive`는 사용하지 않는다. wrapper는 오래된 Background plist나
다른 background 호출에서도 PM2 Node CLI만 macOS 앱 자원 정책으로 실행한다.

wrapper는 전용 `PM2_HOME` 안의 PID lock으로 CLI 호출도 직렬화한다. 여러 브라우저 탭,
Control Server와 터미널 명령이 겹쳐도 God daemon을 중복 생성하지 않는다. 일반 상태
snapshot은 10초간 재사용하며 start/stop/restart 뒤에는 즉시 강제 갱신한다.

```bash
scripts/pm2ctl.sh jlist
```

### SSE는 연결됐지만 로그가 없음

프로세스가 실제로 stdout/stderr에 새 줄을 쓰는지 확인하세요. SSE 연결은 데이터가
없을 때도 주기적으로 keepalive ping을 보냅니다.
