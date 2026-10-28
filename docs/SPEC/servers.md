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
- crash 복구와 반복 실패의 backoff/재시도 한도는 서비스의 `restart` 설정을 PM2
  manifest에 전달합니다. 기본값은 기존 정책을 유지합니다. `max_restarts`는 짧은
  실행 뒤 실패하는 불안정한 시작의 한도이며 시간당 crash 횟수 제한이 아닙니다.
  명시적 stop을 crash로 취급해 부활시키지 않습니다.
- `max_memory_restart_mb: 0`은 메모리 재시작을 끕니다. 양수는 PM2가 관리하는 루트
  PID 기준이며 화면의 부모·자식 RSS 합계와 다릅니다. PM2 주기 검사라 hard cap이 아닙니다.
- controller 시작 시 `autostart: true` 서비스를 background에서 확인합니다.
  마지막 명시적 중지 의도가 저장된 서비스는 제외합니다. 기존 online 서비스는 유지합니다.

운영 기본값은 `CONTROL_PROCESS_BACKEND=pm2`입니다. `create_app()`의 기본 `native`는
테스트·구형 source rollback 호환 경로입니다. native 제거 계획은 [TODO](../TODO.md)에
남아 있으므로 현재 코드가 완전히 제거됐다고 설명하지 않습니다.

## 종료와 설정 변경

PM2 stop의 primary signal은 `SIGINT`입니다. 설정된 timeout과 `SIGTERM` fallback을
거친 뒤 최종 강제 종료는 PM2가 수행합니다. stop 성공은 listener와 추적 자식이 남지
않았는지 확인한 결과여야 합니다. `always_on`, stop/restart 표시 위치와 확인 정책은
UI 위험 액션 배치에 반영하며 설정을 보존합니다.

PM2 항목이 중지됐더라도 미리 확인한 PID·생성 시각의 프로세스나 listener가 남거나
관측 권한이 없으면 성공으로 처리하지 않습니다. 추적하지 못한 detached 자식의 부재까지
보증하지 않으며, 불명확한 PID에 추가 종료 신호를 보내지 않습니다.

신호 허가와 종료 관측은 PID·생성 시각의 같은 정밀도 비교를 사용합니다. PID 미확인
listener 증거와 조회 불명을 빈 포트로 바꾸지 않으며 IPv6 loopback도 관측합니다.
전용 HOME에 미지원 이름의 PM2 항목이 있으면 runtime 조회와 조작을 거부합니다. 외부 종료는
관리 root와 관측한 자식 PID도 보호하며, 자식 신원 조회 불명 시 종료를 거부합니다.

실행 중 정의 제거 또는 command/cwd/env/종료 계약·PM2 재시작 정책·로그 출력 대상 변경은
reload에서 거부합니다.
먼저 서비스를 중지한 뒤 편집합니다. 상세 절차는 [설정](configuration.md)에 있습니다.

## 장애 이력과 선택적 health 복구

`ServiceOperations`는 운영 시작 시 10초 이상 간격으로 공유 PM2 snapshot을 관측합니다.
서비스별 최근 100개 사건을 `runtime/service-operations/events.json`에 원자적으로 저장하며
파일은 private 권한입니다. 정의의 env/command나 로그 원문은 저장하지 않습니다.
수동 실행 성공과 관측한 online/종료/재시작을 구분합니다. PM2 재시작 횟수에는 수동
재시작과 엔진 전환도 포함될 수 있으므로 crash 횟수라고 표시하지 않습니다.
종료 시각은 관측 시각이며 실제 종료 원인·정확한 발생 시각을 추정하지 않습니다.

`recovery.enabled`는 기본 false입니다. 켠 서비스만 시작 유예시간 이후의 연속된
새 health 실패를 세고, PID/create_time/시작 시각과 정의·health를 재검증한 뒤 PM2에
재시작을 요청합니다. cooldown과 시간당 한도는 private 파일에 보존하므로 controller
재시작으로 초기화되지 않습니다. health 복구 확인까지 성공해야 복구 완료로 기록합니다.
중지·외부·identity 불명·엔진 점검/미복구 상태에서는 자동 재시작하지 않습니다.
기능을 끈 서비스에는 이 기능을 위한 health 요청을 추가하지 않습니다.

## PM2 daemon 교체 후 복구

운영 observer는 성공한 새 snapshot과 동일한 daemon의 PID·OS 생성 시각을 대조합니다.
PID 파일은 locator이며 사용자·전용 HOME의 daemon 신원까지 확인합니다. 파일이 없거나
신원이 불명이면 상태 목록은 제공하되 자동 복구를 승인하지 않습니다.
실행·중지 의도, 정의의 hash, 마지막 관리 PID/생성 시각과 복구 결과를 기존 private
`events.json`에 보존합니다. 명시적 시작/중지 성공과 최초 확인된 PM2 상태로 의도를
기록하며, 목록에서 사라진 사실만으로 실행 의도를 바꾸지 않습니다.
명시적 시작/중지/재시작과 외부 종료는 조작 전에 의도를 내구성 있게 저장합니다.
저장 실패는 409로 거부하고 신호·spawn을 하지 않습니다. 손상되거나 읽을 수 없는
상태는 신규 설치로 취급하지 않고 자동 시작을 막으며 원본 파일을 보존합니다.
자동 시작 비활성으로 저장한 정상 차단 사유도 재시작 시 그대로 읽습니다. 검증·로딩과
writer가 같은 허용 사유 집합을 사용하며 기존 의도 파일을 삭제하거나 초기화하지 않습니다.

구형 stop 사건과 의도 미확정 항목의 기존 stopped PM2 상태는 autostart 대상 선택 전에
편입합니다. 이미 저장한 시작 의도는 이관으로 덮지 않습니다. 손상된 정의·PID 생애·복구
예산 필드가 있으면 값을 버리는 대신 자동 조작을 차단하고 원본을 보존합니다. 명시적
그룹 시작은 이미 online이어도 실행 의도를 저장하며 불필요한 spawn은 하지 않습니다.

daemon이 교체되고 마지막 의도가 실행인 서비스가 PM2 목록에서 사라졌을 때 기존
PM2 시작 API를 의존 순서대로 호출합니다. 일반 crash/수동 stop/errored 항목을
이 복구로 부활시키지 않습니다. 수동 중지는 controller 재시작과 복구 대기보다 우선합니다.
중단된 복구는 다음 controller에서 재평가하며 정의 변경·엔진 점검 중에는 실행하지 않습니다.
autostart에도 같은 정의·신원·충돌 검사와 저장된 시도 예산을 적용하며 대상 선택 이후의
수동 중지를 세대로 재검증합니다. 일반 health 복구 역시 중지 의도를 존중합니다.

중복 방지를 위해 이전 관리 PID가 종료됐고 설정 포트가 비었는지 확인하며, 포트를
열기 전인 새 orphan도 찾도록 사용자 프로세스의 명령/cwd를 대조합니다. exec wrapper의
최종 명령을 모르면 `adopt_command` 선언 전까지 복구를 막습니다. 직접 실행하는 shebang
launcher 역시 최종 프로세스 신원이 모호하므로 차단합니다. 외부
프로세스가 남거나 신원이 불명확하면 기본은 `blocked`로 남기며 자동 종료·입양하지 않습니다.
명시적으로 켠 `recovery.auto_takeover`의 제한된 전환 조건은 아래 절차를 따릅니다.
포트 없는 worker는 잔여 자식 부재를 입증할 수 없어 자동 복구를 막고 수동 확인을 요구합니다.
시작 시도는 daemon당 최대 3회, 시도 사이 최소 60초이며 disk에 보존합니다. online과
설정 health 확인까지 통과해야 `restored`입니다. 화면에서 복구 상태·차단 이유와 이력을
확인할 수 있습니다. 외부 프로세스 정리와 운영 배포/실서비스 수용은 별도 작업입니다.
`CONTROL_PM2_AUTOMATIC_START=0`은 autostart·daemon 복구·health 자동 재시작을 막는
관찰 전용 운영 선택입니다. 명시적 사용자 액션은 그대로 사용할 수 있습니다.

특정 서버의 작업 큐·진행률은 다루지 않습니다. 앱의 메모리 상태를 복원하려면 해당
앱이 저장과 재개를 지원해야 합니다. PM2의 `save`/`resurrect`는 엔진 전환에도 사용하지만,
일반 장애에서는 오래된 dump를 일괄 실행하지 않고 현재 정의·의도·포트와 대조합니다.

## 서비스 그룹과 선행 서비스

`group`은 선택한 표시 이름이며 Servers 화면에서 그룹 시작/중지/재시작을 요청할 수
있습니다. `depends_on`은 서비스 ID 목록입니다. 알 수 없는 ID, 자기 자신, 중복,
순환 참조는 설정 검증에서 거부합니다. 그룹 이름은 앞뒤 공백·제어문자·`/`가 없는
80자 이하 문자열이고 빈 값은 그룹 미지정입니다.

시작은 선행 서비스부터, 중지는 역순으로 수행합니다. 시작 후 online과 설정된 health를
최대 30초 확인하며 선행 실패 시 후속 서비스는 blocked로 남습니다. 그룹 밖의 선행
서비스를 임의 시작하지 않으며 정상 준비 상태를 요구합니다. 그룹 밖에서 실행 중인
후속 서비스가 의존하는 대상을 중지하지 않습니다. 개별 중지/재시작에도 이 보호가
적용됩니다. 그룹 재시작은 처음부터 중지 상태인 서비스를 시작하지 않습니다.
등록된 시작/중지/재시작 action의 enabled 정책을 지킵니다.

요청은 background job이며 화면은 활성 job만 조회합니다. 그룹 작업은 한 번에 하나만
실행하고 최근 50개 결과를 메모리에 보관합니다. controller 재시작을 넘어 job을 재개하지
않습니다. PM2 backend에서만 지원하며 일회성 Action Group과 구별합니다.
`autostart`도 의존 순서를 지키며 autostart=false인 선행 서비스를 임의 시작하지 않습니다.

## 외부 프로세스

PM2가 소유하지 않는 프로세스의 PID/명령/cwd와 health가 확인돼야 `running_external`입니다.
신원은 맞지만 health가 실패하면 `unhealthy`, 포트 점유·응답만 있고 신원이 확인되지
않으면 `unknown`입니다. `unmanaged`는 소유 여부를 별도로 나타내므로 세 상태 모두
true일 수 있으며 상세에서 PID·health·진단을 함께 표시합니다. PM2 운영 경로는 자동
입양하지 않습니다. `cmdline_mismatch`, `cwd_mismatch`, `health_failed` 등 실제 진단을
유지하며 healthy 응답만으로 정상 실행이나 입양 성공이라고 판단하지 않습니다.

`adopt_command`, `adopt_match`와 `unmanaged_policy`는 기존 설정/API 호환을 위해
보존합니다. 기본 매칭은 exact이며 prefix는 명시한 expected argv 뒤의 trailing 인자만
허용합니다. prefix가 cwd/health/PID 검사까지 완화하지 않습니다.

macOS framework Python의 `bin/python*` launcher와 같은 framework/version의
`Resources/Python.app/Contents/MacOS/Python`은 실제 파일이 모두 확인될 때 같은 실행
파일로 정규화합니다. venv symlink도 같은 경로로 해석하며 인자·cwd·health·PID 생성
시각 검증은 유지합니다. 다른 framework/version이나 다른 인자는 허용하지 않습니다.

외부 종료는 health가 활성화된 안전 검증 대상에만 허용합니다. 종료 직전에도
PID/create_time, holder, cmdline/cwd/health를 다시 확인하고 controller나 다른 관리
서비스를 종료하지 않습니다. 대상 확인 없이 외부 프로세스를 종료하는 우회 기능을
추가하지 않습니다.
관리 밖 프로세스에 대한 일반 PM2 시작/중지/재시작은 API에서도 409로 거부합니다.
명시적 외부 종료 성공은 중지 의도로 기록하여 대기 중인 자동 복구가 되살리지 않게 합니다.
`cancel_recovery`는 프로세스에 신호를 보내지 않고 중지 의도만 저장합니다. 외부 종료가
허용되지 않는 unhealthy/신원 불명 상황에서도 자동 복구를 취소할 수 있습니다.
외부 종료는 평가한 PID·생성 시각을 유지하고, 전용 PM2의 다른 관리 PID를 금지합니다.
실행 파일과 cwd를 엄격히 확인하며 listener 해제만으로 종료 성공이라 하지 않습니다.
동일 프로세스 생존이 불명이면 실패로 남기고 신호를 확대하지 않습니다.

## 외부 인스턴스의 PM2 관리 전환

`PM2 관리로 전환`은 기존 PID 입양이 아니라 외부 인스턴스 종료 후 현재 정의로 PM2에서
다시 실행하는 명시적 작업입니다. 기본은 수동이며 확인 창은 종료와 작업 중단 가능성을
안내합니다. 상세 GET에서 확인한 PID·생성 시각을 고정해 요청하며 신원이 바뀌면 거부합니다.
PM2 backend, 포트·정상 health, 동일 사용자·실행 파일·인자·cwd·생성 시각, 활성 시작 액션,
선행/의존 서비스 조건, 정상 daemon과 실행 manifest를 종료 전에 확인합니다.
PM2 관리 항목이 이미 있으면 중지/오류 항목이어도 전환하지 않습니다.

설치·엔진 작업과 공통 관리 잠금, config reconciliation·서비스 잠금을 전환 전체에 유지합니다.
종료 전 중지 의도를 저장하고 종료·포트 해제를 확인한 다음 시작 의도를 저장해 PM2로 실행합니다.
health까지 확인해야 성공입니다. 종료/시작/health 실패는 구분하며 unmanaged spawn으로
우회하지 않습니다. 중간에 controller가 종료되면 자동으로 다른 PID를 종료하지 않습니다.
외부 종료 후 시작 실패 시 기존 PID·메모리 작업은 복구할 수 없으며 현재 상태와 로그를 확인합니다.

서비스별 `recovery.auto_takeover`는 기본 false이며 health 실패 복구의 `enabled`와 별개입니다.
재시작 뒤 작업 재개가 가능한 서비스에서만 명시적으로 켭니다. 이전 PM2 실행 의도·소유 PID와
정의가 저장돼 있고 daemon 교체를 확인한 뒤에만 작동합니다. 기존 소유 PID 생애가 그대로
남았거나 새 daemon 생성 전 30초 안에 생성된 신원 일치 프로세스를 대상으로 하며 daemon
생성 후 5분 안에만 허용합니다. 일반 외부 발견·수동 중지·정의 변경·기존 PM2 항목·신원/health
불명·전역 자동 실행 비활성은 차단합니다. 자동 종료 시도는 daemon당 최대 한 번이며 저장된
복구 예산을 소비합니다. 실패 후 반복 종료하지 않고 상태·전환 이력을 남깁니다.

## 상태 조회와 URL

데스크톱 목록은 SERVICE, PORT / URL, RUNTIME, STATUS 순서입니다. PORT / URL과
RUNTIME에 STATUS보다 넓은 공간을 배정하며, RUNTIME의 PM2 재시작 횟수는 `0`처럼
숫자만 표시합니다. CPU·RAM·재시작 횟수 보조 행 내부는 줄바꿈하지 않습니다.
상세 패널에서는 재시작 횟수의 의미와 관측 이력을 확인할 수 있습니다.

목록은 정상 PM2 snapshot을 재사용하고 TTL 경과 시 한 번의 background refresh를
시작합니다. 최초 snapshot이 없으면 잠시 `unknown`, 갱신이 지연되거나 실패하면
직전 상태와 경고를 표시합니다. mutation은 stale snapshot만으로 성공 처리하지 않습니다.
운영 snapshot은 기존 wrapper 잠금 아래 공식 PM2 API를 통해 필요한 상태 필드만 받아
Python으로 env 원문을 전달하지 않습니다. 별도 상주 Node bridge를 두지 않습니다.
응답 필드와 상태값은 [HTTP API](../../API.md#서비스-데이터)에 정의합니다.

메인과 Servers 목록의 `PORT / URL` 영역은 `open_url`이 있으면 클릭 또는 Enter·Space로
해당 웹페이지를 새 탭에 바로 엽니다. 행 펼침 상태는 바꾸지 않으며 모바일 펼침 패널에도
같은 바로 열기를 제공합니다. URL이 없으면 일반 정보로 표시합니다.

URL 열기는 로컬 host로 설정된 주소를 원격 dashboard의 hostname에 맞춰 치환합니다.
이미 지정한 외부 host는 유지합니다. 이 동작은 서비스의 listen 주소를 바꾸지 않으므로
원격 접속을 허용할 대상 서비스가 접근 가능한 인터페이스에 listen해야 합니다.

## 코드와 검증

- `backend/pm2_manager.py`: `Pm2Manager`, `build_manifest`, `reconcile_service_definitions`
- `backend/service_operations.py`, `backend/routes/operations.py`: 관측·복구·그룹 job
- `backend/health_checker.py`, `backend/process_manager.py`: 외부 진단과 종료 안전 검사
- `backend/routes/api.py`, `backend/app.py`: 상태/action API, background autostart
- `frontend/src/features/dashboard/`, `frontend/src/utils/openServiceUrl.ts`: 행·확인·URL
- 관련 회귀: `test_pm2_manager.py`, `test_pm2_api.py`, `test_kill_external.py`,
  `test_autostart.py`, `test_pm2_integration.py` (`backend/tests/`)

실제 PM2 통합 검사와 운영 수용의 차이는 [개발 안내](../development/setup.md)에 있습니다.
