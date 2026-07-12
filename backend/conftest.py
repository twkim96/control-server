"""Pytest 공용 설정.

`backend/`를 sys.path에 넣어 각 모듈을 직접 import할 수 있게 한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
