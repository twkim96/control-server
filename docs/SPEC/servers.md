# Servers: 장기 실행 서버

## 소유권과 실행

Servers는 HTTP/TCP 서버, worker, gateway, tunnel처럼 계속 살아 있는 프로세스를 관리합니다.
언어나 실행 파일 형식으로 분류하지 않습니다. 일회성 명령은 [Services](actions.md)에 둡니다.

- YAML이 정의의 원본이며, 운영 프로세스 상태는 전용 PM2 daemon에서 읽습니다.
- `server-control--<service_id>` 이름, fork 모드, 단일 instance로 실행합니다.
  `command[0]`과 나머지 argv를 셸 문자열로 합치지 않습니다.
- shell wrapper는 최종 서버를 `exec`해야 신호와 PID 추적이 유지됩니다.
- 시작 전 cwd, 실행 파일과 포트 점유를 검사합니다. 다른 프로세스가 같은 포트를 사용하면
  중복으로 시작하지 않습니다.
- crash 복구와 반복 실패의 backoff/재시도 한도는 PM2 manifest에서 설정합니다.
  명시적 stop을 crash로 취급해 부활시키지 않습니다.
- controller 시작 시 `autostart: true` 서비스를 background에서 확인합니다.
  `autostart: false`인 중지 서비스를 임의 시작하지 않으며 기존 online 서비스는 유지합니다.

운영 기본값은 `CONTROL_PROCESS_BACKEND=pm2`입니다. `create_app()`의 기본 `native`는
테스트·구형 source rollback 호환 경로입니다. native 제거 계획은 [TODO](../TODO.md)에
남아 있으므로 현재 코드가 완전히 제거됐다고 설명하지 않습니다.

## 종료와 설정 변경

PM2 stop의 primary signal은 `SIGINT`입니다. 설정된 timeout과 `SIGTERM` fallback을
거친 뒤 최종 강제 종료는 PM2가 수행합니다. stop 성공은 listener와 추적 자식이 남지
않았는지 확인한 결과여야 합니다. `always_on`, stop/restart 표시 위치와 확인 정책은
UI 위험 액션 배치에 반영하며 설정을 보존합니다.

실행 중 정의 제거 또는 command/cwd/env/종료 계약 변경은 reload에서 거부합니다.
먼저 서비스를 중지한 뒤 편집합니다. 상세 절차는 [설정](configuration.md)에 있습니다.

## 외부 프로세스

같은 포트에 PM2가 소유하지 않는 인스턴스가 있으면 `running_external`로 표시합니다.
PM2 운영 경로는 자동 입양하지 않습니다. 명령·cwd·health 검사에 통과한 외부 인스턴스는
`pm2_exclusive`, 실패한 경우는 `cmdline_mismatch`, `cwd_mismatch`, `health_failed` 등
실제 진단을 유지합니다.

`adopt_command`, `adopt_match`와 `unmanaged_policy`는 기존 설정/API 호환을 위해
보존합니다. 기본 매칭은 exact이며 prefix는 명시한 expected argv 뒤의 trailing 인자만
허용합니다. prefix가 cwd/health/PID 검사까지 완화하지 않습니다.

외부 종료는 health가 활성화된 안전 검증 대상에만 허용합니다. 종료 직전에도
PID/create_time, holder, cmdline/cwd/health를 다시 확인하고 controller나 다른 관리
서비스를 종료하지 않습니다. 대상 확인 없이 외부 프로세스를 종료하는 우회 기능을
추가하지 않습니다.

## 상태 조회와 URL

목록은 정상 PM2 snapshot을 재사용하고 TTL 경과 시 한 번의 background refresh를
시작합니다. 최초 snapshot이 없으면 잠시 `unknown`, 갱신이 지연되거나 실패하면
직전 상태와 경고를 표시합니다. mutation은 stale snapshot만으로 성공 처리하지 않습니다.
응답 필드와 상태값은 [HTTP API](../../API.md#서비스-데이터)에 정의합니다.

메인과 Servers 목록의 `PORT / URL` 영역은 `open_url`이 있으면 클릭 또는 Enter·Space로
해당 웹페이지를 새 탭에 바로 엽니다. 행 펼침 상태는 바꾸지 않으며 모바일 펼침 패널에도
같은 바로 열기를 제공합니다. URL이 없으면 일반 정보로 표시합니다.

URL 열기는 로컬 host로 설정된 주소를 원격 dashboard의 hostname에 맞춰 치환합니다.
이미 지정한 외부 host는 유지합니다. 이 동작은 서비스의 listen 주소를 바꾸지 않으므로
원격 접속을 허용할 대상 서비스가 접근 가능한 인터페이스에 listen해야 합니다.

## 코드와 검증

- `backend/pm2_manager.py`: `Pm2Manager`, `build_manifest`, `reconcile_service_definitions`
- `backend/health_checker.py`, `backend/process_manager.py`: 외부 진단과 종료 안전 검사
- `backend/routes/api.py`, `backend/app.py`: 상태/action API, background autostart
- `frontend/src/features/dashboard/`, `frontend/src/utils/openServiceUrl.ts`: 행·확인·URL
- 관련 회귀: `test_pm2_manager.py`, `test_pm2_api.py`, `test_kill_external.py`,
  `test_autostart.py`, `test_pm2_integration.py` (`backend/tests/`)

실제 PM2 통합 검사와 운영 수용의 차이는 [개발 안내](../development/setup.md)에 있습니다.
