# Control Server 문서

현재 checkout의 사양과 운영 지침을 찾는 시작점입니다. 버전과 배포 포함 목록은 루트
`package.json`, 설정 필드와 기본값은 `backend/config_schema.py`, `backend/config_loader.py`,
`backend/config.example.yml`이 기준입니다. 공개 릴리스와 남은 수용 검증은
[TODO](TODO.md), 과거 릴리스 결과는 [CHANGELOG](../CHANGELOG.md)를 확인합니다.

## 기능별 사양

| 변경 영역 | 문서 | 내용 |
| --- | --- | --- |
| 장기 실행 서버 | [Servers](SPEC/servers.md) | PM2 소유권, 시작·종료, 외부 프로세스, 상태와 URL 열기 |
| 설정과 데이터 | [설정](SPEC/configuration.md) | YAML 편집, reload 보호, checkpoint, 경로 제한 |
| 일회성 작업 | [Services](SPEC/actions.md) | Action Group, 실행·취소, 이력과 출력 보존 |
| 로그 | [로그](SPEC/logs.md) | 서비스 회전, 실행별 보관, SSE와 화면 수명 |
| 리소스 | [리소스 측정](SPEC/resources.md) | 부모·자식 CPU/RSS, 초기·부분 측정 |
| 외형 | [외형 설정](SPEC/appearance.md) | 서버 저장, 브라우저 캐시, 색상 프리셋 |
| 독립 엔진 | [PM2 엔진](SPEC/pm2-engine.md) | 명시적 업데이트, 상태 보존, recovery journal |

## 개발·운영·연동

- [개발 환경과 검증](development/setup.md)
- [설치](operations/install.md), [앱 업데이트와 rollback](operations/updating.md),
  [제거](operations/uninstall.md)
- [source checkout 운영과 문제 해결](operations/source-runtime.md),
  [관리형 설치 복구](operations/recovery.md), [릴리스 절차](operations/releasing.md)
- [HTTP API](../API.md): 인증, JSON/SSE 응답과 오류 계약
- [서비스 등록 계약](integrations/service-registration.md): Servers와 Services의 구분
- [Tailscale HTTPS wrapper](integrations/tailscale-https.md)
- [보안 정책](../SECURITY.md), [기여 안내](../CONTRIBUTING.md)

`API.md`는 기존 npm 배포물의 공개 경로를 유지하는 HTTP 레퍼런스입니다.
[pre-PM2 복구 근거](history/pm2-migration-recovery.md)는 과거 source checkout의
태그·백업에 대한 기록이며 현재 관리형 설치의 rollback과 구분합니다. 이전 경로는
공개 링크나 설치기가 참조하는 안내 파일만 유지합니다.
로컬 `phase.md`, `todo.md`, `update_*.md`는 작성 당시의 기록으로 Git과 npm
배포에서 제외하며, 현재 지침이나 두 번째 작업 목록으로 사용하지 않습니다.
