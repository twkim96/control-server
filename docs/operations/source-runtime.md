# Source checkout 운영과 문제 해결

npm 관리형 설치의 [설치](install.md)·[복구](recovery.md)와 다른 경로입니다.
개발 준비는 [개발 안내](../development/setup.md)를 먼저 적용합니다.

## LaunchAgent 소유권 사전 확인

source `launchd/install.sh`에는 관리형 `lib/installer.mjs`의 소유권 검사가 없습니다.
`render`/`load`는 기존 plist를 `mv -f`로 교체하고, `unload`는 동명 plist를 제거하며,
`restart`는 unload 후 load합니다. 실행 전에 **사용자가** 대상 label·plist·loaded job의
소유 경로를 확인해야 합니다. `render`도 읽기 전용 확인 명령이 아닙니다.

기본 label은 `com.twkim.server-control`, plist는
`~/Library/LaunchAgents/com.twkim.server-control.plist`입니다. 다음은 읽기 전용 확인 예시입니다.
파일이 없다면 두 plutil 명령은 생략하고, loaded job도 없는지 별도로 확인합니다.
전체 plist나 launchctl 출력을 표시하면 비밀번호가 포함될 수 있어 아래 필드만 읽습니다.

```bash
/usr/bin/plutil -extract WorkingDirectory raw -o - \
  "$HOME/Library/LaunchAgents/com.twkim.server-control.plist"
/usr/bin/plutil -extract ProgramArguments json -o - \
  "$HOME/Library/LaunchAgents/com.twkim.server-control.plist"
launchctl print "gui/$(id -u)/com.twkim.server-control" 2>/dev/null \
  | sed -n -E '/^[[:space:]]*(path|program|working directory) =/p'
```

- `WorkingDirectory`와 `ProgramArguments`의 Python/backend/config/runtime 경로를 현재
  source checkout과 비교합니다. `~/.control-server/current`나 다른 managed home을
  가리키면 관리형 설치의 agent입니다. 다른 checkout 경로면 그 checkout의 agent입니다.
- 디스크 plist뿐 아니라 loaded job의 plist/program/working directory 경로도 대조합니다.
  출력이 없거나 소유 경로가 서로 다르면 확인되지 않은 상태로 취급합니다. 관리형
  `install.json`의 `launchd_label`과 custom `CONTROL_LAUNCHD_LABEL`/`CONTROL_PLIST_DST`가
  있으면 위 확인 대상도 그 실제 label·경로로 바꿉니다.
- 관리형 또는 다른 checkout이 동명 agent를 소유하면 source의 `render/load/restart/unload`와
  `build_and_reload.sh --all`을 실행하지 않습니다. 기본 source label로 실행하려면 agent가
  없거나 같은 checkout 소유라는 확인과 포트/runtime 충돌 없음이 필요합니다.
- 관리형 설치를 유지하며 개발할 때는 [개발 안내](../development/setup.md)의 foreground
  실행을 별도 config·포트·log/runtime·PM2_HOME으로 분리하고 `autostart`를 검토합니다.
  기존 운영 서비스 정의·포트를 복제해 시작하지 않습니다. 병행 source LaunchAgent는
  별도 label/plist와 같은 자원 격리를 설정하고 그 새 대상의 소유권까지 먼저 확인해야 합니다.
- 기본 label의 소유권을 source로 이전하려면 승인된 중단·백업·전환 절차를 먼저 확정합니다.
  스크립트 호출 자체가 그 이전을 안전하게 수행하는 것은 아닙니다.

관리형 CLI의 `assertNoForeignLaunchAgent`는 기존 plist에 해당 managed `current` 경로가
있는지 검사해 불일치를 거부합니다. 이 검사는 관리형 CLI를 통한 활성화/제거 경로에
적용되며 source script를 직접 실행할 때 적용되지 않습니다.

## launchd와 PM2

아래 변경 명령은 사전 소유권 확인과 자원 격리 조건을 충족한 source 설치에만 사용합니다.

```bash
bash launchd/install.sh load
bash launchd/install.sh status
bash launchd/install.sh restart
bash launchd/install.sh unload
```

`launchd/run.env`와 plist 권한을 사용자만 읽을 수 있도록 유지합니다.
설정 선택은 `SERVER_CONTROL_CONFIG=/path/to/config.yml bash launchd/install.sh restart`로
지정할 수 있습니다. config 경로를 바꿔도 label/plist의 소유권 충돌이 해결되지는 않습니다.

Control Server는 launchd, 장기 서버는 전용 PM2, Action은 자체 runner가 맡습니다.
wrapper는 전용 `PM2_HOME`, Node와 CLI를 선택하며 global `~/.pm2`를 사용하지 않습니다.
관리형 설치에서는 현재 release wrapper와 해당 전용 home을 사용합니다.

```bash
bash scripts/pm2ctl.sh status
bash scripts/pm2ctl.sh logs server-control--SERVICE_ID
```

Control Server LaunchAgent는 `ProcessType=Standard`입니다. wrapper는 macOS 자원 정책과
전용 PID lock으로 CLI 호출을 직렬화하여 background 호출 지연과 daemon 중복 생성을
방지합니다. 일반 snapshot은 짧게 재사용하고 mutation 뒤 확정 조회합니다.
raw `jlist`/dump/manifest는 환경 비밀값을 포함하므로 공개 터미널 기록에 출력하지 않습니다.

`bash scripts/build_and_reload.sh`는 frontend만 build합니다. `--all`은 controller도
재시작하므로 서비스 작업과 운영 영향 확인 뒤 명시적으로 사용합니다.

## 문제 해결

| 증상 | 확인과 다음 단계 |
| --- | --- |
| 서비스 시작 실패 | `backend/logs/<service_id>.log`, cwd/entry/실행 파일, 의존성, 포트 점유 확인 |
| launchd 실패 | `_launchd.err`, `_launchd.out`, `launchd/install.sh status`; 비밀번호·frontend/dist·venv·config 확인 |
| `running_external` | 상세 `adopt_diagnostics`와 cmdline/cwd/health 확인; PM2가 자동 입양하지 않는 것은 정상 |
| `PM2 command timed out: jlist` | 아래 snapshot 경고 계약과 wrapper 상태 확인 |
| SSE 연결만 있고 로그 없음 | 실제 stdout/stderr 출력 확인; 데이터가 없는 keepalive는 정상 |
| reload 409 | 실행 중 정의 변경/제거 또는 orphan을 확인하고 [설정 사양](../SPEC/configuration.md) 순서로 처리 |

PM2 조회 실패 시 마지막 정상 snapshot과 경고를 보여줍니다. refresh가 2초 이상 지연되거나
실패하면 경고가 나타나며 최초 snapshot 전에는 `unknown`일 수 있습니다. 시작·종료는
PM2의 확정 응답 없이 성공 처리하지 않습니다. background autostart가 listener 기동을
느린 상태 조회에 묶지 않습니다.

`_launchd.err`의 `PM2 command slow`, `timed out`, `returncode`, `stderr_bytes`를 확인합니다.
명령 출력 원문과 환경변수는 진단 로그에 추가하지 않습니다.

## 복구 구분

현재 관리형 app/engine 복구는 [관리형 복구](recovery.md), source의 pre-PM2 cutover
복구 근거는 [과거 복구 기록](../history/pm2-migration-recovery.md)입니다. 후자는 당시 Git tag와 private
snapshot을 사용하는 역사적 절차이며 현재 앱 rollback과 엔진 rollback을 대신하지 않습니다.
