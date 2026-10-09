# Tailscale HTTPS wrapper

`scripts/with_tailscale_https.sh`는 Tailscale CLI로 인증서·개인키를 준비하고 환경변수로
경로를 넘긴 뒤 `--` 다음 명령을 `exec`합니다. 대상 애플리케이션이 TLS 환경변수를
지원해야 합니다. Control Server 자체의 TLS termination을 제공하는 기능은 아닙니다.

```yaml
command:
  - "/bin/bash"
  - "/path/to/control-server/scripts/with_tailscale_https.sh"
  - "--"
  - "/path/to/project/.venv/bin/python"
  - "-u"
  - "app.py"
env:
  TAILSCALE_HTTPS_DOMAIN: "device.example-tailnet.ts.net"
```

서비스 등록 위치와 안전한 stop 계약은 [서비스 등록](service-registration.md)을 따릅니다.
이 wrapper는 source 운영 도구이며 현재 npm allowlist에는 포함되지 않습니다. npm 설치
경로에 당연히 존재한다고 가정하지 말고 관리 대상 프로젝트의 wrapper 경로를 사용합니다.

## 환경 설정과 실패

- `TAILSCALE_HTTPS_DOMAIN` 또는 `HTTPS_DOMAIN`이 필수이며 실제 사설 도메인은 private config에 둡니다.
- `TAILSCALE_BIN`으로 CLI 경로를, `TAILSCALE_HTTPS_CERT_DIR`,
  `TAILSCALE_HTTPS_CERT_FILE`, `TAILSCALE_HTTPS_KEY_FILE`로 저장 위치를 지정합니다.
- 대상의 TLS 환경변수 이름은 `TAILSCALE_HTTPS_ENABLED_ENV`,
  `TAILSCALE_HTTPS_CERT_ENV`, `TAILSCALE_HTTPS_KEY_ENV`로 지정합니다.
  기본 대상 이름은 `HTTPS`, `SSL_CERT_FILE`, `SSL_KEY_FILE`입니다.
- `TAILSCALE_HTTPS_MIN_VALIDITY`가 인증서 갱신 요청의 최소 유효기간입니다.
  기계적 기본값과 `HTTPS_*` fallback은 script가 기준입니다.
- 도메인/CLI 부재, 잘못된 환경변수 이름, cert 명령 실패, PEM 누락은 실행을 거부합니다.
  wrapper 자체의 무한 재시도는 없고 장기 서버 retry는 PM2 정책을 따릅니다.
- 임시 파일을 정리하고 개인키는 0600으로 저장합니다. 개인키·인증서·실제 도메인과
  서비스 자격증명은 Git/배포 문서에 추가하지 않습니다.

`https` YAML의 env 주입과 HTTP health `verify_ssl`은
`backend/config_loader.py`, `backend/pm2_manager.py`의 계약도 함께 확인합니다.
