# 리소스 측정

서비스 CPU/RAM은 PM2 `monit`의 루트 PID 값으로 대체하지 않습니다.
`ResourceSampler`가 PM2 PID를 출발점으로 부모와 자식 process tree를 측정합니다.
controller 표시에는 controller 자신의 샘플을 사용합니다.

- CPU는 `(pid, create_time)`별 누적 CPU 시간의 변화량을 실제 측정 구간으로 나누고
  합산합니다. PID 재사용과 자식 생성·종료를 고려하며 TTL과 구간은 monotonic clock입니다.
- 최초 실제 구간 전 CPU `null`/`—`는 정상입니다. 멀티코어 합계는 100%를 넘을 수 있습니다.
- RAM은 부모·자식 RSS 합계입니다. 공유 페이지가 중복 포함될 수 있어 고유 물리 메모리
  사용량으로 설명하지 않습니다.
- 일부 자식 열거·측정 실패는 `partial`과 발견/측정/누락 개수로 알립니다. UI의
  `일부 누락` 표시를 측정 성공으로 숨기지 않습니다.
- 짧은 TTL cache를 공유하고 config reload 뒤 제거된 서비스의 이력을 정리합니다.

필드와 unavailable 사유는 [HTTP API](../../API.md#runtimeinfo)가 정의합니다.
특정 시점의 CPU/RSS 수치로 PM2 전환의 부하 감소를 일반화하지 않습니다.
과거 전환 기록에서 동조건 latency 기준선이 없어 남은 비교는 [TODO](../TODO.md)에 있습니다.

코드: `backend/resource_sampler.py`, `backend/routes/system.py`, `backend/routes/api.py`,
`frontend/src/features/dashboard/ServerRow.tsx`, `frontend/src/features/shell/AppHeader.tsx`.
회귀 검사는 `backend/tests/test_resource_sampler.py`입니다.
