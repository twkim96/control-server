# 로그와 SSE

## 서비스 로그

PM2는 stdout/stderr를 `<log_root>/<service_id>.log`에 합칩니다. 개별 out/error 파일은
`/dev/null`로 보내 중복 로그를 만들지 않습니다. native 호환 경로는 append OS fd를
자식에 직접 넘겨 controller 중계 PIPE에 의존하지 않습니다.

`max_bytes`는 hard cap이 아닙니다. 기본 동작은 PM2 stopped 확인 뒤와 stopped 서비스를
다시 시작하기 직전에 크기를 평가해 `.log.1`부터 `keep`개까지 회전하는 것입니다.
계속 실행하는 파일은 설정값보다 커질 수 있습니다. controller 자체를 재시작했다고
모든 서비스 로그가 정리되지 않습니다. `log.enabled: false`는 PM2 출력 대상을
`/dev/null`로 설정하며 출력 대상 변경은 중지 후 저장해야 합니다.

### 선택적 실행 중 회전

PM2 7.1 이상에서 `log.rotate_while_running`을 켜면 운영 관측 주기에 파일 크기를
확인합니다. 임계값 아래에서는 회전을 위한 PM2 CLI를 호출하지 않습니다.
회전은 기존 `pm2ctl.sh`의 engine maintenance/CLI 잠금 안에서 파일을 `.log.pending`으로
옮기고 PM2 `reloadLogs`로 fd를 다시 엽니다. daemon이 pending 파일의 fd를 닫은 것이
확인된 뒤에만 보관 파일 이동/삭제·선택적 gzip 압축(`log.compress`)을 수행합니다.
plain/gzip은 같은 `keep` 보관 슬롯을 공유하며 실행 중 회전의 압축 옵션입니다.

재개방·압축 실패 시 pending 원본은 남고 다음 관측에서 재시도합니다. 엔진 전환과
회전은 같은 잠금으로 배제됩니다. 임계값은 검사 간격·출력 속도만큼 초과할 수 있습니다.
SSE는 열린 이전 fd를 끝까지 읽고 새 inode로 전환합니다. fd 재개방 완료 표식을
압축 완료와 분리하므로 압축이 실패해도 새 파일의 화면 스트림은 계속됩니다. 느린
클라이언트가 여러 회전을 건너뛰면 세대 번호로 이를 감지해 누락 안내를 표시합니다.
보관 수 밖의 오래된 로그까지 스트림 전달을 보장하지는 않습니다. 서버 재시작이나 controller를
경유하는 stdout 중계 프로세스는 추가하지 않습니다. 날짜 만료·전체 디렉터리 hard cap은
없습니다. 격리 실제 PM2 검사와 운영 장기 수용은 구분합니다.

### 헬스체크 로그 표시

로그창은 이미 받은 줄에서 등록된 HTTP health URL의 정확한 경로와 일치하는 정상
GET/HEAD access 로그(2xx/3xx)만 기본 숨깁니다. `헬스체크 표시`로 다시 볼 수 있습니다.
식별 가능한 일반 access/JSON 형식만 처리하고 실패 응답·오류·알 수 없는 형식은 남깁니다.
이 기능은 화면 표시 필터입니다. 원본 로그는 그대로 저장하고 추가 health 요청이나
수집기·저장 전 출력 필터를 두지 않습니다. 각 서버가 자체 파일에 저장하는 로그에는
영향을 주지 않습니다.

## Action 로그와 외부 로그

실행별 파일은 `<log_root>/actions/<group_id>/<item_id>/<run_id>.log`입니다. EOF까지
가능한 출력 drain과 종료 footer 기록 뒤, 같은 item의 완료 파일을 `keep_runs` 한도로
정리합니다. 활성 캡처는 다른 실행의 보관 정리에서 제외합니다. `keep_runs: 0`은 자동
파일 삭제를 하지 않으며 실행 중 파일의 개별 크기 제한도 없습니다.

외부 로그는 item의 `external_logs`에 정확히 등록된 경로이며 허용 루트 안인 파일만
조회할 수 있습니다. 임의 경로 tail API로 확장하지 않습니다.

## 화면과 스트림

- 실행 중 서비스 drawer는 SSE, 중지 서비스는 한 번의 tail 조회를 사용합니다.
- 실행 중 Action은 SSE와 metadata polling을 사용하고 최종 상태 뒤 metadata polling을
  중지합니다. 완료 로그는 기본 tail을 한 번 읽습니다.
- 로그 append는 짧은 batch로 묶고 화면별 maxLines를 적용합니다. 이 상한은 브라우저
  표시량이며 디스크 파일 크기 제한이 아닙니다.
- drawer를 닫으면 연결과 누적 화면 상태를 정리합니다. 숨겨진 탭의 일반 목록 polling도
  멈춥니다. 고속 로그·stopped SSE의 실제 브라우저 수용은 [TODO](../TODO.md)에 남아 있습니다.

offset tail, SSE `line`/`ready`/`end`, keepalive와 인증은
[HTTP API](../../API.md#로그와-sse)를 참고합니다.

## 코드와 검증

- `backend/log_manager.py`: `rotate_if_needed`, `read_since`, `stream`
- `backend/pm2_manager.py`: `_rotate_service_log`, `rotate_running_logs`
- `backend/pm2_log_rotation.py`, `lib/pm2-probe.mjs`, `scripts/pm2ctl.sh`: 잠금 아래 재개방
- `backend/run_log_manager.py`: `stop_capture`, `_enforce_keep_runs`
- `backend/routes/api.py`, `routes/actions.py`
- `frontend/src/hooks/useBufferedLogLines.ts`, `useLogStream.ts`, `useActionRunStream.ts`
- `frontend/src/features/logs/LogDrawer.tsx`, `features/services/ActionRunDrawer.tsx`
- 관련 회귀: `backend/tests/test_log_manager.py`, `test_action_runner.py`, `test_actions_routes.py`,
  `test_pm2_integration.py`
