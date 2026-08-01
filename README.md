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
- Runtime: macOS launchd (Control Server), isolated PM2 (managed services, v1.4.3)
- Configuration: YAML

프로젝트의 HTTP API는 [API.md](./API.md)를 참고하세요.

## 등록 위치 결정 — AI 에이전트 필독

사용자가 “이 웹서버를 컨트롤서버에 넣어줘”, “서버 목록에 추가해줘”라고 요청하면
기본 의미는 **Servers에 장기 실행 서비스로 등록**하는 것입니다. 서비스마다 Control
Server 프런트엔드의 새 탭·라우트·전용 화면을 만들거나, 일회성 명령용 Services에
등록하지 마세요.

| 대상 | 등록 위치 | 사용 API |
| --- | --- | --- |
| 포트를 열고 계속 실행되는 HTTP/TCP 서버, worker, gateway, tunnel | **Servers** | `POST /api/config/services` |
| update, deploy, sync, scan, build처럼 실행 후 끝나는 명령 | **Services**의 Action Group | `POST /api/config/actions` |
| 장기 서버와 그 서버의 배포/업데이트 명령 | 서버는 **Servers**, 명령은 **Services** | 두 API를 각각 사용 |
| Control Server 자체 기능 | 기존 화면에 통합할 수 없고 사용자가 명시적으로 요청한 경우에만 UI 개발 | 코드 변경 |

Python, Go, Node, Bun, shell wrapper 등 구현 언어는 분류 기준이 아닙니다. **계속 살아
있어야 하는 프로세스인지, 한 번 실행하고 종료되는 명령인지**로 판단합니다. 관리 대상 웹
페이지는 Control Server 안에 iframe이나 새 탭으로 끼워 넣지 않고 서비스의 `open_url`을
`URL 열기`로 엽니다.

### AI의 서버 등록 작업 순서

가능하면 YAML을 직접 편집하는 대신 인증된 **서비스 설정 API**를 사용합니다. API는
필드 검증, PM2 runtime 정의 조정, last-good checkpoint 저장, registry reload를 한
요청 흐름에서 처리합니다.

1. 대상 프로젝트에서 실제 `cwd`, 실행 명령, 포트, 사용자 화면 URL, health URL,
   정상 종료 signal을 확인합니다. 추측한 경로나 포트를 등록하지 않습니다.
2. `GET /api/config/services`로 기존 ID·포트·등록 내용을 확인합니다.
3. 로그인 세션의 `GET /api/auth/me` 응답에서 `csrf_token`을 받고, mutation에
   `X-CSRF-Token`을 사용합니다.
4. 신규는 `POST /api/config/services`, 수정은 `PUT /api/config/services/<id>`로
   전체 서비스 payload를 보냅니다.
5. API가 2xx를 반환하면 registry reload까지 이미 완료된 것입니다. 성공 뒤
   `POST /api/config/reload`를 다시 호출하지 않습니다.
6. `GET /api/config/services`와 `GET /api/services/<id>`로 저장 결과와 런타임 상태를
   확인합니다.
7. “등록” 요청만 받았다면 서비스를 임의로 시작하지 않습니다. 실행 요청도 받았을 때만
   `POST /api/services/<id>/actions/start`를 호출하고 health/포트를 확인합니다.

`backend/config.yml`을 직접 수정해도 되지만 fallback으로 취급합니다. 직접 수정한 경우에만
`POST /api/config/reload`를 호출하고, 2xx 응답과 `GET /api/config/services` 결과를
확인하세요. reload가 `409 config_reload_blocked`를 반환하면 PM2 상태를 우회하거나
Control Server를 임의 재시작하지 말고 실행 중 서비스와 변경 필드를 먼저 확인합니다.

### 표준 서비스 액션

서비스별 표현을 action label에 넣지 말고 다음 ID·타입·라벨·순서를 그대로 사용합니다.
DevSpace 같은 gateway도 `Gateway 상태`, `통합 로그`, `Gateway 열기`로 바꾸지 않습니다.

| 순서 | ID | type | 표준 라벨 | 포함 조건 |
| ---: | --- | --- | --- | --- |
| 1 | `start` | `process_start` | `시작` | 기본 |
| 2 | `stop` | `process_stop` | `중지` | 기본 |
| 3 | `restart` | `process_restart` | `재시작` | 기본 |
| 4 | `health` | `health_check` | `상태 확인` | health를 사용할 때 |
| 5 | `logs` | `show_logs` | `로그` | log를 사용할 때 |
| 6 | `open` | `open_url` | `URL 열기` | `open_url`이 있을 때 |

PM2 backend의 stop primary signal은 `SIGINT`로 고정합니다. 기본 수동 서비스는
`timeout_seconds: 5`, `confirm_required: false`, fallback은
`["SIGTERM", "SIGKILL"]`을 사용합니다. 장시간 정리가 필요한 서비스만 실제 종료 동작을
확인한 뒤 timeout을 늘립니다.

### 서비스 payload 기본값

- `id`: 안정적인 소문자 `snake_case` 권장. 생성 후 임의로 바꾸지 않습니다.
- `name`: 화면에 표시할 짧은 서비스명. action label에는 반복하지 않습니다.
- `cwd`: 실제 프로젝트 절대 경로이며 `controller.allowed_path_roots` 안이어야 합니다.
- `entry_file`: 화면 표시와 진단용 실제 진입 파일입니다.
- `command`: shell 문자열이 아닌 argv 배열입니다. Python은 프로젝트 venv의 절대 경로를
  우선 사용합니다.
- `env`: 값은 문자열로 보내며 비밀번호·토큰을 문서나 커밋에 복사하지 않습니다.
- `port`: 실제 listen 포트입니다. `port_env_name`은 앱이 그 환경변수를 읽을 때만
  지정합니다.
- `open_url`: 사용자가 열 실제 화면 URL입니다. health endpoint를 대신 넣지 않습니다.
- `health.url`: 가능하면 짧게 2xx를 반환하는 `/health` 또는 `/healthz`를 사용합니다.
- `lifecycle`: 별도 요청이 없으면 `manual`, `autostart: false`,
  `unmanaged_policy: status_only`를 사용합니다.

정확한 JSON 스키마와 인증 오류는 [API.md의 서비스 등록 계약](./API.md#자동화ai-서비스-등록-계약)을
참고하세요.

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
        label: "시작"
        type: "process_start"
        enabled: true
      - id: "stop"
        label: "중지"
        type: "process_stop"
        enabled: true
        strategy:
          signal: "SIGINT"
          timeout_seconds: 5
          confirm_required: false
          fallback: ["SIGTERM", "SIGKILL"]
      - id: "restart"
        label: "재시작"
        type: "process_restart"
        enabled: true
      - id: "health"
        label: "상태 확인"
        type: "health_check"
        enabled: true
      - id: "logs"
        label: "로그"
        type: "show_logs"
        enabled: true
      - id: "open"
        label: "URL 열기"
        type: "open_url"
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

### 리소스 측정

서비스 CPU/RAM 표시는 PM2의 루트 PID `monit` 값이 아니라 Control Server의 독립
프로세스 트리 측정값을 사용합니다. CPU는 `(pid, create_time)`별 변화량을 합산해 자식
생성·종료와 PID 재사용에 따른 왜곡을 줄이고, TTL과 측정 구간은 monotonic clock으로
계산합니다. 첫 실제 구간 전 CPU는 `—`가 정상이며 멀티코어 합계는 100%를 넘을 수
있습니다.

RAM은 부모·자식의 **RSS 합계**로, 공유 페이지가 중복 포함될 수 있습니다. 상세 화면의
`일부 누락`은 자식 열거 또는 접근 실패로 값이 불완전함을 뜻합니다. 리소스 이력은 config
reload 뒤 현재 서비스와 controller 항목만 남기고 정리합니다.

PM2 운영 모드의 `process_stop` primary signal은 `SIGINT`여야 하며, 설정된 `SIGTERM`
fallback 뒤 마지막 `SIGKILL`은 PM2가 수행합니다. 로그의 `max_bytes`/`keep` 회전은 PM2가
stopped를 확정한 뒤 또는 stopped 서비스를 다시 시작하기 직전에 적용됩니다. PM2의
별도 out/error 파일은 `/dev/null`로 보내므로 통합 로그만 보관합니다. 실행 중인 서비스의
command/cwd/env/종료 timeout 변경과 정의 제거는 orphan·stale crash restart 방지를 위해
reload가 409로 거부되므로 먼저 서비스를 중지하세요. 거부된 후보 config는 private
`backend/runtime/last_good_config.yml`에서 즉시 원복되며, 재시작 때도 private manifest와
대조합니다. 1.4.2 첫 적용에서 기존 manifest가 이전 정의이면 Servers 탭에 재시작 필요
서비스를 표시하고, config 밖의 stopped legacy entry만 background에서 안전하게 정리합니다.

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
scripts/pm2ctl.sh status
```

수동 점검에 raw `jlist`를 사용하지 마세요. PM2 application의 전체 `pm2_env`가 출력돼
터미널 scrollback에 비밀번호나 서비스 자격증명이 남을 수 있습니다.

### SSE는 연결됐지만 로그가 없음

프로세스가 실제로 stdout/stderr에 새 줄을 쓰는지 확인하세요. SSE 연결은 데이터가
없을 때도 주기적으로 keepalive ping을 보냅니다.
