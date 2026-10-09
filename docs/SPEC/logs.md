# 로그와 SSE

## 서비스 로그

PM2는 stdout/stderr를 `<log_root>/<service_id>.log`에 합칩니다. 개별 out/error 파일은
`/dev/null`로 보내 중복 로그를 만들지 않습니다. native 호환 경로는 append OS fd를
자식에 직접 넘겨 controller 중계 PIPE에 의존하지 않습니다.

`max_bytes`는 실행 중 hard cap이 아닙니다. PM2 stopped 확인 뒤와 stopped 서비스를
다시 시작하기 직전에 크기를 평가해 `.log.1`부터 `keep`개까지 회전합니다.
기본 5 MiB/3개 설정이라도 계속 실행하는 파일은 훨씬 커질 수 있습니다. controller
자체를 재시작했다고 모든 서비스 로그가 정리되지 않습니다. 날짜 만료, 전체 디렉터리
용량 상한, 압축 또는 실행 중 주기적 회전은 구현하지 않았습니다.

실행 중 파일에 무조건 rename 회전을 적용하지 않습니다. 자식의 열린 fd와 이후 로그
조회가 다른 파일을 바라볼 수 있기 때문입니다. 조건부 후속 검토는 [TODO](../TODO.md)에
있습니다.

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
- `backend/pm2_manager.py`: `_rotate_service_log`
- `backend/run_log_manager.py`: `stop_capture`, `_enforce_keep_runs`
- `backend/routes/api.py`, `routes/actions.py`
- `frontend/src/hooks/useBufferedLogLines.ts`, `useLogStream.ts`, `useActionRunStream.ts`
- `frontend/src/features/logs/LogDrawer.tsx`, `features/services/ActionRunDrawer.tsx`
- 관련 회귀: `backend/tests/test_log_manager.py`, `test_action_runner.py`, `test_actions_routes.py`,
  `test_pm2_integration.py`
