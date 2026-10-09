# 독립 PM2 엔진

## 명시적 업데이트

초기 앱 번들의 PM2 7.0.3은 고정 fallback입니다. 사용자가 별도로 설치한 엔진은
`runtime/pm2-engine/releases/<version>`에 두고 `runtime/pm2-engine/current`로 선택합니다.
앱 `current`와 다르며 기존 전용 `PM2_HOME`은 그대로 유지합니다.

Settings를 열면 상태 GET 뒤 최신 버전 확인을 한 번 수행합니다. 확인 버튼은 이를
갱신할 뿐 엔진을 설치하지 않습니다. 설치·전환은 업데이트 버튼이나
`control-server pm2 update [--home PATH]`로만 요청합니다. 지원되지 않는 backend/설치나
필수 Node/worker 부재는 상태의 `supported`/`reason`으로 설명합니다.

상태에는 선택 엔진, 실제 daemon 버전, 마지막 최신 확인과 background job이 있습니다.
앱 release 변경과 엔진 변경의 수명주기를 혼동하지 않습니다.
API 보호와 202/최종 job 판정은 [HTTP API](../../API.md#pm2-엔진-관리-153)에 정의합니다.

## 전환과 복구

새 엔진을 먼저 준비하고 전용 daemon과 서비스 snapshot을 복원합니다. controller는
launchd 아래에 남으며 관리 서비스에는 짧은 재시작이 발생할 수 있습니다. 성공은 실제
daemon 버전, 이전 실행/중지 상태와 전환 전에 정상인 health target을 확인한 결과입니다.
동일 엔진의 재요청으로 정상 실행 PID를 불필요하게 교체하지 않습니다.

엔진 cutover는 `recovery.json` journal을 사용합니다. 실패 시 이전 엔진과 snapshot으로
복구를 시도하고 미완료 journal은 보존합니다. 다음 명시적 요청은 복구를 먼저 수행할 수
있으며 `failed`와 `rolled_back=true`는 요청한 업그레이드 성공을 뜻하지 않습니다.
복구만 완료되면 새 업데이트는 별도 요청합니다. 절차는
[관리형 복구](../operations/recovery.md#failed-or-interrupted-pm2-engine-update-153)를 따릅니다.

앱 포인터/metadata 변경에는 이 engine journal이 적용되지 않습니다. 실제 pre-1.5.3 앱
rollback의 독립 엔진 호환은 아직 검증되지 않았습니다.

## 코드와 검증

- `backend/pm2_engine.py`, `backend/routes/system.py`
- `lib/pm2-engine.mjs`, `lib/pm2-probe.mjs`, `lib/transaction.mjs`, `scripts/pm2ctl.sh`
- `frontend/src/features/settings/Pm2EngineSettings.tsx`, `frontend/src/api/system.ts`
- 자동 회귀: `backend/tests/test_pm2_engine_api.py`, `tests/cli/pm2-engine.test.mjs`
- 실제 격리 전환: `backend/tests/test_pm2_engine_integration.py`
- 과거 로컬·운영 수용 기록: [CHANGELOG](../../CHANGELOG.md#153---unreleased).
  현재 release CI와 구버전 수용의 남은 조건은 [TODO](../TODO.md)에서 관리합니다.
