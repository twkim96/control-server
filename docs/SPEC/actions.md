# Services: 일회성 작업

Services의 Action Group은 update, deploy, sync, scan, build처럼 종료되는 명령을 담습니다.
장기 실행 서버의 start/stop 액션과 다른 개념이며 [Servers](servers.md)로 대체하지 않습니다.

## 목록 표시

메인 화면 하단과 Services 페이지는 등록된 Action Group을 개수 제한 없이 모두 표시합니다.
메인에서도 그룹 추가·편집·실행과 순서 변경을 제공합니다.

## 실행과 취소

- 그룹과 item 정의는 YAML에 저장합니다. `kind: python`은 필수 cwd에서 Python 명령을,
  `kind: argv`는 셸을 거치지 않는 argv 명령을 실행합니다.
- 등록과 실행 시점 모두 command를 검증합니다. 위험한 executable, 셸 직접 실행과
  인터프리터의 `-c`/`-e` 평가 형식을 차단합니다. 임의 스크립트의 내부 동작을 격리하는
  sandbox는 아닙니다.
- `ActionRunner`가 `shell=False`, 새 process session으로 실행하고 run_id를 발급합니다.
  동일 item의 반복 실행은 각각 별도 run입니다. PM2로 이관하지 않습니다.
- 취소는 해당 process group에 `SIGINT`를 보낸 뒤 timeout 시 `SIGKILL`로 정리합니다.
  `cancelled` 표시만으로 종료를 확정하지 않고 실제 `ended_at`을 기준으로 합니다.
- 로그를 끈 작업은 stdout/stderr를 `DEVNULL`로 폐기하여 읽지 않는 PIPE가 차는 일을 막습니다.

## 이력과 보존

실행 상태는 `running`, `succeeded`, `failed`, `cancelled`입니다. run metadata는 메모리에
있으며 controller 재시작 뒤 복원하지 않습니다. 완료된 metadata만 메모리 한도에 맞춰
정리하며 활성 Popen, waiter와 취소 진행 중인 작업의 제어 정보는 보존합니다.

디스크 실행 로그는 별도로 남습니다. 파일 보관과 외부 로그 조회는 [로그 사양](logs.md)에
있습니다. 외부 로그 목록의 편집과 그룹 정렬은 현재 UI/API에 구현되어 있으며,
과거 계획에서 보류했다는 이유로 새 미완료 작업으로 되살리지 않습니다.

## 코드와 검증

- `backend/action_runner.py`: `ActionRunner`, `_prune_completed_locked`, `cancel`
- `backend/routes/actions.py`, `run_log_manager.py`: 실행 API와 로그
- `frontend/src/features/services/ActionGroupForm.tsx`, `ActionGroupCard.tsx`, `ActionRunDrawer.tsx`
- 관련 회귀: `backend/tests/test_action_runner.py`, `test_actions_routes.py`, `test_action_loader.py`
- 요청·응답·SSE 이벤트: [HTTP API](../../API.md#action-실행-데이터)
