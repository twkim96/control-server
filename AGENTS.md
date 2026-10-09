# Control Server 작업 지침

## 문서 진입점

- 현재 사양은 [docs/SPEC.md](docs/SPEC.md), 미완료 작업은 [docs/TODO.md](docs/TODO.md).
- 문서 정리·유지는 `project-wiki`를 사용한다. 관련 feature/guide만 읽고 구현·테스트를 대조한다.
- 변경과 함께 해당 사양·운영 지침·남은 수용을 갱신한다. 완료와 관측 대기를 구분한다.
- `README.md`는 소개와 quick start, `CHANGELOG.md`는 릴리스 기록이다.
- `API.md`는 기존 npm 공개 경로를 유지하는 HTTP 계약의 원본이다.
- 이전 경로는 확인된 소비자가 있을 때만 남긴다. `docs/INSTALL.md`, `docs/UPDATING.md`,
  `docs/RECOVERY.md`, `docs/UNINSTALL.md`, `docs/AI_REGISTRATION.md`는 공개 npm 1.5.2와
  GitHub README가 참조하므로 짧은 안내 파일로 유지한다. 새 내부 링크는
  `docs/operations/`, `docs/integrations/`의 현행 문서로 직접 연결한다.
  `docs/RELEASING.md`는 확인된 참조가 없어 제거했으며 [릴리스 절차](docs/operations/releasing.md)가 원본이다.
- pre-PM2 source 복구 근거는 [과거 복구 기록](docs/history/pm2-migration-recovery.md)에 둔다.
  루트 `PM2_RECOVERY.md`는 설치기의 필수 `APP_COPY_ENTRIES` 항목이자 기존 공개 링크
  대상이므로 짧은 안내 파일로 유지한다. 현재 복구는 [관리형 복구](docs/operations/recovery.md)를 따른다.
- Git-ignored `phase.md`, `todo.md`, `update_*.md`는 역사와 로컬 증거이며 현재 작업 목록은 아니다.
  private 계획·snapshot을 공개 docs로 통째로 옮기지 않는다.

## 작업과 데이터 보존

- 먼저 Git 상태를 확인하고 다른 작업자의 미커밋·untracked 변경을 보존한다.
  stage가 필요한 요청에서는 확인된 경로만 추가하며 broad add/reset을 하지 않는다.
- 문서 초기화는 commit/push/publish, 서비스 start/stop/restart, TODO 구현 승인이 아니다.
- 실제 config/run.env/비밀키/runtime/log와 raw PM2 출력은 읽기·공유 범위를 제한하고 커밋하지 않는다.
- 시작과 종료의 구체적 안전 계약은 [Servers](docs/SPEC/servers.md),
  reload 보호는 [설정](docs/SPEC/configuration.md)을 따른다.
- global PM2나 사용자 `~/.pm2`를 조작하지 않는다. 해당 설치의 `scripts/pm2ctl.sh`와
  전용 `CONTROL_PM2_HOME`을 사용한다. raw jlist의 inherited env를 출력하지 않는다.
- ActionRunner 일회성 명령을 장기 서버 PM2 lifecycle과 혼합하지 않는다.
- 관리형 app release와 config/runtime/log, app pointer와 engine pointer를 구분한다.
  purge, private snapshot 삭제나 다른 프로젝트 수정은 그 정확한 범위의 승인이 필요하다.
- 읽기 전용 조사·문서 작업에서 실서비스를 켜거나 실제 메시지를 보내 검증하지 않는다.

## 효율과 검증

- 설정·setup script는 [개발 안내](docs/development/setup.md)부터 확인한다.
- 독립 검색/읽기는 병렬로 묶고 결과를 각각 확인한다. generated/private runtime은 필요할 때만 읽는다.
- 변경 없는 결과를 재사용하고 실패한 영향 파일부터 재검증한다. 같은 build를 포함하는 aggregate를
  반복하지 않는다. 환경/fixture 실패 때문에 무관한 제품 코드나 assertion을 바꾸지 않는다.
- 문서·copy·style의 가벼운 변경은 diff/링크 또는 유용한 기존·visual 검사로 검증한다.
  구체적 동작 결함·데이터/보안/동시성 위험은 실제 trigger와 결과를 검증하는 회귀를 추가한다.
- 자동 검사, 격리 실제 PM2, 설치된 app, 물리 장비와 운영 브라우저 결과를 따로 보고한다.
  CI 성공은 해당 commit에서만 유효하며 현재 미커밋 변경의 증거가 아니다.
- subagent는 독립 범위와 end-to-end 비용 절감이 있을 때만 쓴다. 정확한 cwd/소유 파일/
  입력·완료 조건을 넘기고 다른 변경을 보존하게 한다. 빠른 조회·짧은 편집·결합된 진단은 로컬에서 한다.
  제공된 role 설정을 따르고 불가능한 role을 다른 모델로 몰래 대체하지 않는다.
- 공유 출력은 단일 writer, job은 단일 monitor가 맡는다. 최종 diff와 결정적 결과는 부모가 확인한다.

## 배포 문서 경로

npm allowlist는 root `package.json`의 `files`, release copy는 `lib/installer.mjs`의
`APP_COPY_ENTRIES`가 원본이다. `docs/` 하위 문서 추가·이동 시 패키지 포함과 상대 링크를
확인한다. 공개 문서에 로컬 계획이나 credentials가 섞이지 않도록 한다.
