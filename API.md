# Control Server HTTP API

Control Server의 JSON 및 SSE API 레퍼런스입니다. 현재 Flask 라우트와
`frontend/src/types/*.ts` 타입을 기준으로 작성되어 있습니다.

- 기본 URL: `http://127.0.0.1:9000`
- JSON 응답: `application/json`
- 스트림 응답: `text/event-stream`
- 시간: Unix epoch seconds
- 크기: bytes

## 인증

로그인 후 발급되는 세션 쿠키와 CSRF 토큰을 사용합니다.

- 쿠키 이름: `server_control_session`
- 쿠키 속성: `HttpOnly`, `SameSite=Lax`, `Path=/`
- 세션 수명: 90일
- CSRF 헤더: `X-CSRF-Token`

보호된 GET 및 SSE 요청에는 세션 쿠키가 필요합니다. 보호된 POST, PUT, DELETE 요청에는
세션 쿠키와 CSRF 헤더가 모두 필요합니다. 로그인, 로그아웃, 인증 상태 조회,
`GET /api/meta`, `GET /api/settings/appearance`은 예외입니다.

`CONTROL_PASSWORD`가 설정되지 않으면 보호된 API는 다음 응답을 반환합니다.

```json
{
  "error": "password_not_configured",
  "message": "CONTROL_PASSWORD 환경변수가 설정되지 않았습니다."
}
```

### 인증 엔드포인트

| 메서드 | 경로 | 요청 | 응답 |
| --- | --- | --- | --- |
| POST | `/api/auth/login` | `{"password":"..."}` | `{ok,user,csrf_token}` 및 세션 쿠키 |
| POST | `/api/auth/logout` | 없음 | `{"ok":true}` |
| GET | `/api/auth/me` | 없음 | `{authenticated,user?,csrf_token?}` |

### curl 예시

아래 예시는 `jq`를 사용합니다.

```bash
BASE_URL=http://127.0.0.1:9000
COOKIE_JAR=/tmp/control-server-cookie.txt

LOGIN_RESPONSE=$(curl -sS -c "$COOKIE_JAR" \
  -H 'Content-Type: application/json' \
  -d "{\"password\":\"$CONTROL_PASSWORD\"}" \
  "$BASE_URL/api/auth/login")

CSRF_TOKEN=$(printf '%s' "$LOGIN_RESPONSE" | jq -r '.csrf_token')

# 조회
curl -sS -b "$COOKIE_JAR" "$BASE_URL/api/services" | jq

# 변경 요청
curl -sS -b "$COOKIE_JAR" \
  -H "X-CSRF-Token: $CSRF_TOKEN" \
  -X POST \
  "$BASE_URL/api/services/example/actions/restart" | jq
```

## 엔드포인트 목록

### 메타와 인증

| 메서드 | 경로 | 보호 | 설명 |
| --- | --- | --- | --- |
| GET | `/api/meta` | 없음 | 컨트롤러 주소, 비밀번호 설정 여부, 서비스 수 |
| POST | `/api/auth/login` | 없음 | 로그인 |
| POST | `/api/auth/logout` | 없음 | 현재 세션 삭제 |
| GET | `/api/auth/me` | 없음 | 현재 인증 상태와 CSRF 토큰 |

### 서비스 런타임

| 메서드 | 경로 | 보호 | 설명 |
| --- | --- | --- | --- |
| GET | `/api/services` | 세션 | 서비스와 런타임 목록 |
| GET | `/api/services/<sid>` | 세션 | 서비스 상세 및 입양 진단 |
| POST | `/api/services/<sid>/actions/<aid>` | 세션+CSRF | 서비스 액션 실행 |
| POST | `/api/services/<sid>/kill_external` | 세션+CSRF | 확인된 외부 인스턴스 종료 |
| GET | `/api/services/<sid>/logs` | 세션 | 로그 tail 또는 offset 이후 조회 |
| GET | `/api/services/<sid>/logs/stream` | 세션 | 서비스 로그 SSE |

### 서비스 설정

| 메서드 | 경로 | 보호 | 설명 |
| --- | --- | --- | --- |
| GET | `/api/config/services` | 세션 | 런타임을 제외한 서비스 설정 목록 |
| POST | `/api/config/services` | 세션+CSRF | 서비스 생성 |
| PUT | `/api/config/services/<sid>` | 세션+CSRF | 서비스 수정 |
| DELETE | `/api/config/services/<sid>` | 세션+CSRF | 중지된 서비스 삭제 |
| POST | `/api/config/services/reorder` | 세션+CSRF | `{"order":["id",...]}` 순서 적용 |
| POST | `/api/config/reload` | 세션+CSRF | 디스크의 설정 다시 로드 |
| GET | `/api/files` | 세션 | `path=<dir>` query로 허용된 루트 또는 디렉터리 조회 |

### Action Group 설정

| 메서드 | 경로 | 보호 | 설명 |
| --- | --- | --- | --- |
| GET | `/api/config/actions` | 세션 | Action Group 설정 목록 |
| POST | `/api/config/actions` | 세션+CSRF | Action Group 생성 |
| PUT | `/api/config/actions/<gid>` | 세션+CSRF | Action Group 수정 |
| DELETE | `/api/config/actions/<gid>` | 세션+CSRF | Action Group 삭제 |
| POST | `/api/config/actions/reorder` | 세션+CSRF | `{"order":["id",...]}` 순서 적용 |

### 일회성 Action 실행

| 메서드 | 경로 | 보호 | 설명 |
| --- | --- | --- | --- |
| GET | `/api/actions` | 세션 | 그룹과 최근 실행 목록 |
| POST | `/api/actions/<group_id>/<item_id>/run` | 세션+CSRF | 실행 시작 |
| GET | `/api/actions/runs/<run_id>` | 세션 | `tail=N` query로 실행 정보와 로그 조회 |
| POST | `/api/actions/runs/<run_id>/cancel` | 세션+CSRF | 실행 취소 |
| GET | `/api/actions/runs/<run_id>/stream` | 세션 | 실행 로그 SSE |
| GET | `/api/actions/external_log` | 세션 | 등록된 외부 로그 tail |
| GET | `/api/actions/external_log/stream` | 세션 | 등록된 외부 로그 SSE |

외부 로그 API에는 `group_id`, `item_id`, `path` query parameter가 필요합니다.
`path`는 해당 Action Item의 `external_logs`에 정확히 등록되어 있고
`allowed_path_roots` 안에 있어야 합니다.

### 시스템과 외형

| 메서드 | 경로 | 보호 | 설명 |
| --- | --- | --- | --- |
| GET | `/api/system/python_interpreters` | 세션 | 발견된 Python 인터프리터 |
| GET | `/api/system/controller_resource` | 세션 | 컨트롤러 프로세스 리소스 |
| GET | `/api/settings/appearance` | 없음 | 외형 설정과 저장 여부 |
| PUT | `/api/settings/appearance` | 세션+CSRF | 외형 설정 저장 |
| DELETE | `/api/settings/appearance` | 세션+CSRF | 외형 설정 초기화 |

## 공통 응답

### 메타

`GET /api/meta`

```json
{
  "ok": true,
  "password_configured": true,
  "controller": {
    "host": "127.0.0.1",
    "port": 9000
  },
  "service_count": 2
}
```

### 에러

에러 응답은 HTTP 상태와 `error` 코드를 사용하며, 필요한 경우 `message`와 추가
필드를 포함합니다.

```json
{
  "error": "process_error",
  "message": "port 8080 is already in use"
}
```

| 상태 | 대표 코드 |
| --- | --- |
| 400 | `invalid_body`, `invalid_order`, `config_invalid`, `command_blocked` |
| 401 | `unauthorized`, `invalid_credentials` |
| 403 | `csrf_failed`, `path_not_allowed`, `cwd_not_allowed` |
| 404 | `service_not_found`, `action_item_not_found`, `run_not_found` |
| 409 | `process_error`, `service_running`, `config_reload_blocked`, `action_run_failed` |
| 503 | `password_not_configured` |

## 서비스 데이터

### 목록과 상세

`GET /api/services`는 다음 envelope을 반환합니다.

```json
{
  "services": []
}
```

`GET /api/services/<sid>`는 envelope 없이 단일 `ServiceMeta` 객체를 반환합니다.
목록은 비용이 큰 입양 진단과 포트 상세를 생략하고, 상세 응답은
`runtime.port_check`와 필요한 경우 `runtime.adopt_diagnostics`를 포함합니다.

### ServiceMeta

```jsonc
{
  "id": "example",
  "name": "Example Service",
  "description": "Example HTTP service",
  "cwd": "/path/to/example",
  "entry_file": "app.py",
  "command": ["python", "-u", "app.py"],
  "adopt_command": null,
  "adopt_match": "exact",
  "env": {"PORT": "8080"},
  "port": 8080,
  "port_env_name": "PORT",
  "open_url": "http://127.0.0.1:8080",
  "https": {},
  "health": {},
  "log": {},
  "lifecycle": {},
  "actions": [],
  "runtime": {}
}
```

설정 CRUD의 요청과 응답은 같은 정적 필드를 사용하지만 `runtime`은 포함하지 않습니다.
정확한 클라이언트 타입은 `frontend/src/types/config.ts`와
`frontend/src/types/service.ts`를 참고하세요.

### RuntimeInfo

```jsonc
{
  "state": "running",
  "alive": true,
  "unmanaged": false,
  "unmanaged_pid": null,
  "pid": 1234,
  "pgid": 1234,
  "uptime_seconds": 87.3,
  "health": {
    "enabled": true,
    "ok": true,
    "url": "http://127.0.0.1:8080/health",
    "status_code": 200,
    "elapsed_seconds": 0.012
  },
  "port_check": {
    "port": 8080,
    "open": true,
    "errno": null
  },
  "last_exit_code": null,
  "last_exit_time": null,
  "resource": {
    "available": true,
    "reason": "ok",
    "pid": 1234,
    "sampled_at": 1700000000.0,
    "cpu_percent": 1.2,
    "memory_rss_bytes": 55902208,
    "process_count": 3,
    "children_count": 2
  }
}
```

`state` 값:

| 값 | 의미 |
| --- | --- |
| `running` | 추적 PID 생존, health 비활성 또는 정상 |
| `running_external` | 외부 인스턴스가 포트/health에 응답 |
| `unhealthy` | PID는 생존하지만 health 실패 |
| `starting` | 시작 직후 grace 구간 |
| `stopping` | 중지 진행 중 |
| `stopped` | 추적 PID와 외부 응답이 없음 |
| `unknown` | 시작 이력이 없는 초기 상태 등 |

`resource.reason`은 `ok`, `not_running`, `pid_reused`, `access_denied`,
`no_such_process` 중 하나입니다. CPU의 첫 샘플은 `null`일 수 있습니다.

### 입양 진단

`running_external` 상세 응답에는 자동 입양 실패 이유가 포함될 수 있습니다.

```jsonc
{
  "adopt_diagnostics": {
    "ok": false,
    "reason": "cmdline_mismatch",
    "candidate_pid": 12345,
    "expected_command": ["/bin/bash", "run.sh"],
    "adopt_command": null,
    "actual_cmdline": ["python", "app.py"],
    "cwd_expected": "/path/to/example",
    "cwd_actual": "/path/to/example",
    "health_ok": true
  }
}
```

대표 `reason`:

- `policy_not_manage`
- `no_port`, `no_port_holder`
- `pid_is_self`, `pid_tracked_by_other`
- `proc_inspect_failed`
- `cmdline_mismatch`
- `cwd_resolve_failed`, `cwd_mismatch`
- `health_failed`
- `pm2_exclusive`: PM2 runtime에서는 외부 PID를 자동 입양하지 않음

## 서비스 액션

`POST /api/services/<sid>/actions/<aid>`는 서비스 설정에 등록된 action id를 실행합니다.

| action type | 동작 |
| --- | --- |
| `process_start` | 프로세스 시작 |
| `process_stop` | 설정된 signal/fallback 순서로 종료 |
| `process_restart` | 중지 후 다시 시작 |
| `health_check` | 응답 runtime에 최신 상태 반영 |
| `show_logs`, `open_url`, `edit_config` | UI 처리용 서버 no-op |

성공:

```json
{
  "ok": true,
  "service": {}
}
```

### 외부 인스턴스 종료

`POST /api/services/<sid>/kill_external`은 health가 활성화된
`running_external` 서비스에만 허용됩니다. 서버는 종료 직전에 상태와 PID를 다시
검증합니다.

```jsonc
{
  "ok": true,
  "killed": {
    "pid": 1234,
    "name": "python",
    "signal": "SIGTERM",
    "duration_seconds": 0.4
  },
  "service": {}
}
```

## 로그와 SSE

### 로그 조회

```text
GET /api/services/<sid>/logs?tail=200
GET /api/services/<sid>/logs?since_offset=1024
```

```json
{
  "lines": ["first line", "second line"],
  "offset": 2048
}
```

`since_offset`이 있으면 `tail`보다 우선합니다.

### SSE 형식

모든 SSE `line` 이벤트는 JSON 문자열을 `data`에 담습니다.

```text
event: line
data: {"data": "server started"}

event: ready
data: {"data": ""}

: ping
```

- 서비스 로그: 초기 tail → `ready` → 새 `line`
- Action 실행 로그: `ready` → `line` → 완료 시 `end`
- 외부 로그: 초기 tail → `ready` → 새 `line`
- 데이터가 없으면 약 15초마다 `: ping` keepalive

SSE는 GET 요청이므로 세션 쿠키만 사용합니다.

## Action 실행 데이터

`GET /api/actions`:

```jsonc
{
  "groups": [
    {
      "id": "maintenance",
      "name": "Maintenance",
      "description": "",
      "items": [
        {
          "id": "cleanup",
          "name": "Cleanup",
          "kind": "argv",
          "cwd": null,
          "command": ["tool", "cleanup"],
          "env": {},
          "log": {"enabled": true, "keep_runs": 20},
          "external_logs": [],
          "recent_runs": []
        }
      ]
    }
  ]
}
```

`POST /api/actions/<gid>/<iid>/run`은 `{ok,run}`을 반환합니다.
`GET /api/actions/runs/<run_id>?tail=N`은 `{run,lines,offset}`을 반환하며
`tail`은 0~5000 범위로 제한됩니다.

```jsonc
{
  "run": {
    "run_id": "run-id",
    "group_id": "maintenance",
    "item_id": "cleanup",
    "started_at": 1700000000.0,
    "ended_at": null,
    "exit_code": null,
    "status": "running",
    "log_path": "/path/to/log",
    "cancel_reason": null,
    "duration_seconds": null
  }
}
```

`status`는 `running`, `succeeded`, `failed`, `cancelled` 중 하나입니다.

## 설정 변경 규칙

- 서비스 및 Action 설정 변경은 성공 후 registry를 자동으로 다시 로드합니다.
- 실행 중인 서비스는 삭제할 수 없습니다.
- 서비스 `cwd`와 Python Action `cwd`는 `allowed_path_roots` 안이어야 합니다.
- Action 명령은 등록과 실행 시점에 다시 검증됩니다.
- 설정 파일 쓰기는 백업과 원자적 교체 방식으로 수행됩니다.
- 실행 중인 서비스의 런타임 계약을 깨는 변경은 `config_reload_blocked`로 거부될 수 있습니다.

## 폴링 권장값

- 서비스 목록: 약 3초
- 서비스 상세: 약 2초
- health 결과: 서버에서 짧은 TTL로 캐시
- resource 결과: 약 10초 TTL

서비스 목록의 health probe는 병렬로 준비되며, 프런트엔드는 숨겨진 탭에서 폴링을
중지합니다.
