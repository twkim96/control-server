# 설정과 데이터 보존

## 정의와 저장

`controller`, `services`, `actions`가 YAML 최상위 구성이며 `actions`는 Action Group입니다.
정확한 스키마와 기본값은 `backend/config_schema.py`, `backend/config_loader.py`, 공개
예시는 `backend/config.example.yml`에서 확인합니다. 완전한 필드 예제를 README에 복제하지 않습니다.

웹 등록·편집·삭제·정렬은 YAML 저장 뒤 PM2 reconcile과 registry reload까지 수행합니다.
2xx 응답 뒤 다시 reload할 필요가 없습니다. API 수정은 전체 정적 payload를 전달하며
`runtime`은 저장하지 않습니다. 직접 YAML을 편집한 경우에는 명시적으로 reload합니다.

모든 YAML 변경은 save/reconcile/rollback/응답 구성을 같은 reconciliation lock으로
직렬화합니다. 파일 writer는 백업과 원자적 교체를 사용합니다. round-trip YAML writer가
기존 순서와 가능한 주석을 보존합니다.

## reload 보호

- 실행 중 서비스 삭제와 command/cwd/env/종료 계약 변경을 거부합니다.
- 실패한 후보는 마지막 정상 YAML checkpoint로 디스크까지 복구합니다.
- 성공한 후보를 checkpoint에 저장하고 registry·허용 경로·리소스 cache를 갱신합니다.
- controller 재시작 시 private PM2 manifest와 정의를 대조합니다. 오래된 실행 정의는
  재시작 필요로 표시하며, 설정 밖 stopped entry만 background에서 정리합니다.
  실행 중 orphan은 표시하고 임의 삭제하지 않습니다.
- `409 config_reload_blocked`를 controller 재시작으로 우회하지 않습니다.

현재 응답과 오류 코드는 [HTTP API](../../API.md#설정-변경-규칙)를 참고합니다.

## 경로와 환경

서비스 cwd와 Python Action cwd는 `controller.allowed_path_roots` 안에 있어야 합니다.
경로 탐색·등록·실행과 등록 외부 로그 조회 시 관련 백엔드 검사도 적용합니다.
argv 작업은 별도의 [명령 안전 검사](actions.md)를 거치며 허용 루트를 일반 sandbox로
설명하지 않습니다. Python 실행 파일은 각 대상 프로젝트의 venv 절대경로를 권장합니다.
Python 선택기는 발견된 인터프리터로 `command[0]`을 교체하며 의존성 설치를 보장하지 않습니다.

명시적 app 실행 인자 → 환경변수 → source checkout 기본값 순서로 경로를 선택합니다.
사용 가능한 인자는 `backend/app.py`의 CLI, 환경변수는 `CONTROL_CONFIG_PATH`,
`CONTROL_LOG_DIR`, `CONTROL_RUNTIME_DIR`, `CONTROL_FRONTEND_DIST`입니다.

| 데이터 | source checkout | 관리형 설치 |
| --- | --- | --- |
| 서비스 정의 | `backend/config.yml` | `config/config.yml` |
| 운영 비밀 환경 | `launchd/run.env` | `config/run.env` |
| checkpoint·서명키·외형·PM2 | `backend/runtime/` | `runtime/` |
| 서비스·작업·controller 로그 | `backend/logs/` | `logs/` |

관리형 설치는 데이터가 앱 release 밖에 있으므로 update/rollback에서 유지됩니다.
전체 layout과 백업 대상은 [설치](../operations/install.md),
[복구](../operations/recovery.md#data-recovery)에 있습니다.

native의 `runtime/<service_id>.json`은 구형 PID 추적/rollback 자료입니다. PM2 mode는
이 파일을 운영 상태의 원본으로 사용하지 않습니다. JSON과 private snapshot을 문서
정리나 업데이트 과정에서 삭제하지 않습니다. 자동 정리 정책은 아직 확정되지 않았습니다.

## 코드와 검증

- `backend/config_loader.py`, `config_schema.py`, `config_writer.py`, `config_checkpoint.py`
- `backend/routes/config_api.py`: `_config_reconcile_lock`, `_reload_registry`
- `backend/service_registry.py`, `file_browser.py`, `python_finder.py`
- 관련 회귀: `test_config_loader.py`, `test_config_writer.py`, `test_config_transactions.py`,
  `test_file_browser.py`, `test_reorder.py`, `test_python_finder.py` (`backend/tests/`)
