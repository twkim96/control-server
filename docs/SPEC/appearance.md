# 외형 설정

Settings는 배경·텍스트·강조색을 `#rrggbb` 형식으로 저장하고 CSS 변수를 계산합니다.
기본값과 reset은 라이트 팔레트입니다. 정확한 값은 `backend/appearance_store.py`와
`frontend/src/features/settings/appearance.ts`의 기본 상수가 기준이며 두 값을 함께 유지합니다.

현재 색상은 runtime의 `appearance.json`에 저장하고 브라우저 `server-control.appearance`로
캐시합니다. 서버에 저장된 값은 동기화 시 우선하며 서버 값이 없고 로컬 값이 있으면
로컬 값을 유지합니다. 로그인 전 화면도 공개된 appearance GET으로 같은 외형을 읽을 수
있으며 저장·reset은 인증과 CSRF가 필요합니다.

프리셋 목록은 브라우저 `server-control.appearance.presets`에 있습니다. 기존 목록을
덮어쓰지 않고 `다크모드`를 최초 한 번 제공합니다.
`server-control.appearance.presets.dark-mode-v1` 표식 뒤에는 사용자가 지운 프리셋을
다시 만들지 않습니다. 프리셋의 선택·저장·삭제와 서버 색상 저장은 별개입니다.

코드: `backend/appearance_store.py`, `backend/routes/appearance_api.py`,
`frontend/src/features/settings/appearance.ts`, `SettingsPage.tsx`, `frontend/src/styles/tokens.css`.
회귀 검사: `backend/tests/test_appearance_api.py`.
