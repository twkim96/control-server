"""네트워크/프로세스 프로브 공용 헬퍼.

process_manager와 health_checker가 함께 쓰는 순수 함수 모음. 예전에는
process_manager 안의 언더스코어(_) 비공개 함수였고 health_checker가 그것을 직접
import해 모듈 경계를 깨뜨렸다. 공용 모듈로 분리해 양쪽이 공개 API로 의존하게 한다.

여기 함수들은 상태를 갖지 않고 OS/psutil만 건드린다. 따라서 스레드에서 자유롭게 호출해도
안전하다.
"""

from __future__ import annotations

import socket
import subprocess
from typing import Any

import psutil


def find_port_holder(port: int) -> dict[str, Any] | None:
    """주어진 포트(LISTEN)를 점유 중인 프로세스 정보를 반환.

    macOS에서는 psutil.net_connections이 일반 사용자 권한으로는 다른 프로세스의 정보를
    볼 수 없는 경우가 있어 AccessDenied가 발생한다. 그 경우 lsof로 폴백한다.
    `None`이면 점유자가 없다는 뜻이다.
    """
    info = holder_via_psutil(port)
    if info is not None and info.get("pid") is not None:
        return info
    # psutil이 못 보거나 PID를 주지 않는 경우(macOS 권한): lsof로 보강한다.
    info = holder_via_lsof(port)
    if info is not None:
        return info
    # 마지막 보루: TCP connect로 LISTEN 여부만 확인한다. bind 실패는 TIME_WAIT
    # 같은 listener 없는 상태에서도 날 수 있어 start preflight에는 부적합하다.
    if port_accepts_connections(port):
        return {"pid": None, "name": "unknown", "addr": ""}
    return None


def holder_via_psutil(port: int) -> dict[str, Any] | None:
    try:
        conns = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, PermissionError):
        return None

    for conn in conns:
        if conn.status != psutil.CONN_LISTEN:
            continue
        laddr = conn.laddr
        if not laddr or laddr.port != port:
            continue
        pid = conn.pid
        name = ""
        if pid is not None:
            try:
                name = psutil.Process(pid).name()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return {"pid": pid, "name": name, "addr": getattr(laddr, "ip", "")}
    return None


def holder_via_lsof(port: int) -> dict[str, Any] | None:
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fpcn"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        # lsof는 매치 없을 때 exit 1
        return None

    pid: int | None = None
    name = ""
    for raw in result.stdout.splitlines():
        if not raw:
            continue
        tag, _, value = raw[0], raw[1:2], raw[1:]
        if tag == "p":
            try:
                pid = int(value)
            except ValueError:
                pid = None
        elif tag == "c":
            name = value
    if pid is None:
        return None
    return {"pid": pid, "name": name, "addr": ""}


def port_accepts_connections(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        return sock.connect_ex(("127.0.0.1", port)) == 0
    except OSError:
        return False
    finally:
        sock.close()


def pid_alive(pid: int) -> bool:
    """zombie를 죽은 것으로 간주하는 PID 생존 검사."""
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return False
    try:
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def parse_tcp_target(value: str | None) -> tuple[str | None, int | None]:
    """tcp health 설정 url을 (host, port)로 파싱.

    허용 포맷:
    - "host:port"             — 그대로 사용
    - "tcp://host:port"       — scheme 무시, netloc만 사용
    - "host:port/..."         — 경로/쿼리 무시
    """
    if not value:
        return None, None
    raw = value.strip()
    # scheme 제거
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    # path 제거
    raw = raw.split("/", 1)[0]
    if ":" not in raw:
        return None, None
    host, _, port_str = raw.rpartition(":")
    if not host or not port_str:
        return None, None
    try:
        port = int(port_str)
    except ValueError:
        return None, None
    if not (1 <= port <= 65535):
        return None, None
    return host, port


def tcp_probe(host: str, port: int, timeout: float) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        return sock.connect_ex((host, port)) == 0
    except OSError:
        return False
    finally:
        sock.close()


__all__ = [
    "find_port_holder",
    "holder_via_psutil",
    "holder_via_lsof",
    "port_accepts_connections",
    "pid_alive",
    "parse_tcp_target",
    "tcp_probe",
]
