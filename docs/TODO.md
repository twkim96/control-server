# 미완료 작업

기존 계획에서 남은 작업과 검증만 관리합니다. 문서 초기화는 아래 제품 작업·서비스
조작·공개 게시를 실행한 것이 아닙니다. 과거의 완료 체크박스는 활성 작업으로 복제하지 않습니다.

## 공개 릴리스와 Intel 수용

- [ ] Intel 실제 장비에서 packed install/login/update/doctor/rollback/uninstall을 수용한다.
  x64 CI 성공으로 실기기 완료를 대신하지 않는다. 완료 조건: 데이터 보존과 daemon/
  LaunchAgent 정리까지 기록한다.
- [ ] 승인된 release에서 npm 게시·tag·GitHub Release·remote 정합성을 마무리한다.
  2026-10-06 조회 기준 npm `latest`/`next`는 모두 1.5.2이며 1.5.3은 당시 공개 대기였으며 현재 로컬 기능 버전은 1.6.0이다.
  [릴리스 절차](operations/releasing.md)에 따라 정확한 패키지/commit을 검증한 뒤 진행한다.
- [ ] 1.5.0 기록에서 보류한 `v1.5.0` Git tag/GitHub Release의 작성 여부를 결정한다.
  2026-10-06 로컬 tag/공개 Release 조회에는 없다. 이전 npm 공개 베타 게시 완료와
  서로 다른 작업이다. 작성 시 당시 검증 commit을 확인하고, 폐기 시 결정 근거를 기록한다.

## 앱 rollback과 legacy 호환

- [ ] 실제 1.5.3 이전 앱으로 rollback할 때 독립 PM2 엔진 선택과 실행/중지 상태 보존을
  검증한다. 기존 packed fixture는 1.5.3 코드의 버전만 낮췄다.
  완료 조건: 실제 구버전 패키지로 격리 install/update/rollback 후 daemon·health·데이터를
  확인한다. 검증 전에는 [업데이트 안내](operations/updating.md)의 미검증 한계를 유지한다.
- [ ] PM2 안정화 후 native 장기 서버 spawn/adoption/runtime JSON 경로의 제거 범위를
  결정한다. 현재 `ProcessManager`는 안전 외부 진단과 source rollback에서 사용한다.
  완료 조건: 필요한 helper/DTO를 보존하고 두 번째 운영 소유권을 제거하며 구형
  `runtime/<id>.json` 보존/정리 정책과 [pre-PM2 복구 근거](history/pm2-migration-recovery.md)를 함께 갱신한다.
  이 항목은 로컬 1.4.0 Phase 7의 남은 범위이며 초기화 중 코드를 제거하지 않는다.
- [ ] native 입양 경로를 유지할 경우 입양된 외부 프로세스의 stdout 캡처 한계를 정리한다.
  이미 열린 fd를 새 로그로 redirect하지 못하는 문제는 로컬 `todo.md`의 미완료 항목이다.
  PM2 mode는 자동 입양하지 않으므로 현재 PM2 운영의 개선 항목으로 취급하지 않는다.
  완료 조건: native 제거 시 해당 항목을 함께 닫거나, 유지 시 출력 대상 fd 안내/입양
  거부 후보 중 필요한 정책을 결정하고 실제 로그 관측으로 확인한다.

## 남은 운영·브라우저 수용

- [ ] 앱 종료 직후 전용 PM2 daemon이 종료 신호를 받는 경로를 확인한다. macOS Dock
  종료 승인과 daemon 종료·자동 복구가 연속 관측됐지만 신호 발신자는 확인되지 않았다.
  완료 조건: 실행 주체/종료 신호 전달 경로를 확보하고, 필요한 경우 독립 실행 경계를
  검증한다. 복구 성공을 종료 원인 해소로 취급하지 않으며 파괴적인 재현은 별도 수용한다.
- [ ] PM2 daemon 교체 복구와 외부 상태 판정 변경을 승인된 릴리스로 배포하고 실제
  브라우저·서비스에서 수용한다. 격리 종료 신호 회귀와 운영 수용을 구분한다.
  완료 조건: 수동 중지 보존, 실행 서비스 복구, orphan 차단/이유 표시, 외부 unhealthy·
  신원 불명 표시를 확인한다. 포트 없는 worker의 잔여 자식 확인은 수동 절차로 유지한다.
  Pro 리뷰에서 지적한 startup/중지 의도·저장 실패·강제 조회 경합·shebang orphan·공통
  소유권·외부 종료 신원 검증의 회귀와 실제 운영 수용을 구분한다. 구버전 rollback은
  새 중지 의도를 읽지 않으므로 autostart 억제와 최신 상태 보존을 먼저 확인한다.
- [ ] 1.6.2 관리형 배포와 PM2 7.1.1 전환 이후 운영 브라우저·장기 작업을 수용한다.
  2026-10-28 controller만 먼저 적용해 관리 PID 6개 보존 후, 별도 엔진 전환으로 6개
  재시작과 health 정상·비실행 12개 유지·외부 프로세스 보존·journal 정리를 확인했다.
  완료 조건: 외부 신원 불명 이유와 복구 표시를 브라우저에서 확인하고 각 앱의 진행
  상황/체크포인트 재개는 해당 앱의 계약으로 검증한다. 운영 결과는 CHANGELOG에 둔다.

- [ ] PM2 운영 기능(장애 이력, 서비스별 재시작 정책, health 자동 복구, 그룹·의존 순서,
  실행 중 로그 회전/압축, 헬스로그 표시 필터)의 실제 브라우저·서비스별 opt-in·장기
  운영 수용을 확인한다. 1.6.0 관리형 배포와 PID/health 보존 결과는
  [CHANGELOG](../CHANGELOG.md#managed-acceptance-2026-10-22)에 있으며 추가 수용과 구분한다.
  완료 조건: opt-in 정책을 선택한 서비스에서만 켜고, 수동 중지 보존·오류 로그 표시·
  회전 후 스트림·상태 갱신과 서비스 응답을 확인한다.

- [ ] 고속 Action/서비스 로그에서 batch append, 스크롤, CPU, 연결 수를 관측한다.
  stopped 서비스는 실제 Network에서 SSE가 남지 않고, 완료 Action은 tail 반복/metadata
  polling이 멈추며 drawer 종료 후 연결이 정리되는지 확인한다.
  완료 조건: 코드/정적 검사와 구분한 실제 브라우저 결과를 확보한다
  (기존 1.3.1 기록에 고속·stopped SSE 관측이 남아 있음).
- [ ] PM2 전환의 warm API latency를 같은 서비스 상태·브라우저 조건과 기준선으로 비교한다.
  기존 cutover는 CPU/RSS와 전환 후 latency를 기록했으나 전환 전 동조건 latency 표본이
  없어 20% 상대 gate는 입증하지 못했다. 완료 조건: 각 조건 최소 10분의 비교에서
  지속 20% 악화가 없거나, 기준선 재현 불가 사유와 대체 수용 결정을 명시한다.
- [ ] sleep/wake와 재로그인/재부팅 뒤 PM2 상태·health·로그 및 autostart 의도를 확인한다.
  기존 1.4.0 실기기 checklist에 개별 완료 증거가 없다.
  완료 조건: 중지 서비스가 임의 시작하지 않고 대상 listener/자식이 중복되지 않는 결과를
  기록한다. 서비스 중단이 필요한 검증은 별도 승인된 수용 시간에 진행한다.
- [ ] 초기 실제 대상 서비스의 등록 수용 기록을 닫는다. 로컬 `phase.md` Phase 5의
  대상은 현재 checkout config에 기대 포트와 `always_on`으로 등록되어 있고 PM2 cutover
  기록에도 종료/복원이 있다. 별도 session으로 실행하는 자식까지 정리되는 시연 증거는
  별도로 확인해야 한다. 완료 조건: 기존 기록을 먼저 대조하고 필요한 최소 종료 수용으로
  잔여 자식·중복 listener·UI 확인 절차를 검증한다. 다른 프로젝트 수정은 별도 범위다.

## Python 등록 안내

- [ ] 프로젝트별 venv 권장 안내와 cwd의 `.venv`/`venv` 선택 UX를 마무리한다.
  현재 Python 선택기는 시스템 인터프리터 목록 선택이며 프로젝트 cwd 기반 추천이나
  시스템 Python 경고는 없다. [설정 사양](SPEC/configuration.md)에 권장 가이드는 반영했다.
  완료 조건: 기존 수동 command 입력을 유지하며 필요한 warning/venv 바로 선택을 제공하고
  미설치 환경을 설치됐다고 표시하지 않는다. 선택적인 `python_runtime` 메타 필드는
  구현 전 필요성을 재확인하며 자동 venv 생성/pip 설치로 범위를 넓히지 않는다.

## 관측 조건이 충족될 때 재검토

- [ ] 기존 로그 최적화 후에도 idle CPU·로그 파일·다수 SSE가 실제 병목 또는 운영 허용
  크기 초과로 관측될 때만 추가 부하 상한을 검토한다. 기존 1.3.1/1.3.4 보류 후보는
  read_since chunk, 파일 부재 bounded wait, SSE 연결 수, virtualization,
  polling 간격, stopped health 생략, backoff, Waitress thread,
  HTTP→TCP 전환, port-holder scan UX이다. 코드에는 health TTL/single-flight·공유 worker와
  진단 backoff가 이미 있고 PM2 mode에는 자동 입양이 없어 endpoint 분리 후보는 대체됐다.
  완료 조건: 재현된 병목에 맞는 후보만 선택하고 반응성·복구·로그 누락 영향을 수용한다.
  별도 Node bridge/비공식 PM2 RPC도 CLI spawn 병목이 입증되기 전에는 도입하지 않는다.
