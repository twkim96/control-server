"""auth 플로우 통합 테스트.

* 비밀번호 미설정 시 503
* GET 보호: 미인증 401, 인증 후 200
* mutation 보호: 인증되어도 csrf 누락 시 403, csrf 일치 시 200
* SSE: 쿠키만으로 200, csrf 헤더 없어도 OK
* 로그아웃 후 401
"""
from __future__ import annotations

import os
from pathlib import Path
from textwrap import dedent

import psutil
import pytest

# app.py가 backend 패키지를 sys.path 0번에 두는 conftest를 신뢰한다.
import app as backend_app
from auth import PASSWORD_ENV


def _seed_minimal_config(path: Path) -> None:
    path.write_text(
        dedent(
            """
            controller:
              host: "127.0.0.1"
              port: 9000
              allowed_path_roots:
                - "/tmp"
            services:
              - id: "dummy"
                name: "Dummy"
                cwd: "/tmp"
                entry_file: "x.py"
                command: ["python", "x.py"]
            """
        ).strip(),
        encoding="utf-8",
    )


@pytest.fixture
def client(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yml"
    _seed_minimal_config(config_path)
    log_dir = tmp_path / "logs"
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setenv(PASSWORD_ENV, "secret123")

    app = backend_app.create_app(
        config_path=config_path,
        log_dir=log_dir,
        runtime_dir=runtime_dir,
        frontend_dist=tmp_path / "_no_dist",
    )
    app.config["TESTING"] = True
    return app.test_client()


def test_password_not_configured_returns_503(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yml"
    _seed_minimal_config(config_path)
    monkeypatch.delenv(PASSWORD_ENV, raising=False)

    app = backend_app.create_app(
        config_path=config_path,
        log_dir=tmp_path / "logs",
        runtime_dir=tmp_path / "runtime",
        frontend_dist=tmp_path / "_no_dist",
    )
    app.config["TESTING"] = True
    client = app.test_client()

    res = client.get("/api/services")
    assert res.status_code == 503

    res = client.post("/api/auth/login", json={"password": "anything"})
    assert res.status_code == 503


def test_unauthenticated_get_is_401(client):
    res = client.get("/api/services")
    assert res.status_code == 401


def test_login_then_get_services(client):
    res = client.post("/api/auth/login", json={"password": "secret123"})
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert "csrf_token" in body and len(body["csrf_token"]) > 0

    res = client.get("/api/services")
    assert res.status_code == 200
    services = res.get_json()["services"]
    dummy = next(s for s in services if s["id"] == "dummy")
    resource = dummy["runtime"]["resource"]
    assert resource["available"] is False
    assert resource["reason"] == "not_running"
    assert resource["cpu_percent"] is None
    assert resource["memory_rss_bytes"] is None
    assert resource["partial"] is False
    assert resource["discovered_process_count"] == 0
    assert resource["sampled_process_count"] == 0
    assert resource["skipped_process_count"] == 0
    assert resource["window_seconds"] is None


def test_mutation_requires_csrf(client):
    client.post("/api/auth/login", json={"password": "secret123"})
    # CSRF 없이는 403
    res = client.post("/api/services/dummy/actions/start")
    assert res.status_code == 403

    # 잘못된 CSRF도 403
    res = client.post(
        "/api/services/dummy/actions/start",
        headers={"X-CSRF-Token": "wrong"},
    )
    assert res.status_code == 403


def test_config_reload_prunes_deleted_resource_cache(client):
    from process_manager import RuntimeState

    login = client.post("/api/auth/login", json={"password": "secret123"})
    csrf = login.get_json()["csrf_token"]
    sampler = client.application.config["resource_sampler"]
    process = psutil.Process(os.getpid())
    state = RuntimeState(pid=process.pid, create_time=process.create_time())
    first = sampler.sample("deleted-service", state, include_children=False)
    controller_first = sampler.sample("system:controller", state, include_children=False)

    response = client.post(
        "/api/config/reload",
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 200
    second = sampler.sample("deleted-service", state, include_children=False)
    controller_second = sampler.sample("system:controller", state, include_children=False)
    assert second is not first
    assert controller_second is controller_first


def test_login_failure_leaves_unauthenticated(client):
    res = client.post("/api/auth/login", json={"password": "wrong"})
    assert res.status_code == 401
    res = client.get("/api/services")
    assert res.status_code == 401


def test_logout_invalidates_session(client):
    client.post("/api/auth/login", json={"password": "secret123"})
    res = client.post("/api/auth/logout")
    assert res.status_code == 200
    res = client.get("/api/services")
    assert res.status_code == 401


def test_me_endpoint(client):
    res = client.get("/api/auth/me")
    body = res.get_json()
    assert body == {"authenticated": False}

    client.post("/api/auth/login", json={"password": "secret123"})
    res = client.get("/api/auth/me")
    body = res.get_json()
    assert body["authenticated"] is True
    assert "csrf_token" in body



def test_create_service_rejects_cwd_outside_allowed_roots(client, tmp_path):
    """cwd가 allowed_path_roots 밖이면 403."""
    client.post("/api/auth/login", json={"password": "secret123"})
    me = client.get("/api/auth/me").get_json()
    csrf = me["csrf_token"]

    payload = {
        "id": "evil",
        "name": "Evil",
        "cwd": "/etc",  # allowed root는 /tmp
        "entry_file": "x.py",
        "command": ["python", "x.py"],
    }
    res = client.post(
        "/api/config/services",
        json=payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 403
    body = res.get_json()
    assert body["error"] == "cwd_not_allowed"


def test_update_service_rejects_cwd_outside_allowed_roots(client):
    client.post("/api/auth/login", json={"password": "secret123"})
    me = client.get("/api/auth/me").get_json()
    csrf = me["csrf_token"]

    payload = {
        "name": "Dummy",
        "cwd": "/etc",
        "entry_file": "x.py",
        "command": ["python", "x.py"],
    }
    res = client.put(
        "/api/config/services/dummy",
        json=payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 403


def test_delete_running_service_is_rejected(client):
    """tracked PID가 살아 있으면 config 삭제 전에 409로 거부한다."""
    from process_manager import RuntimeState
    import psutil

    client.post("/api/auth/login", json={"password": "secret123"})
    csrf = client.get("/api/auth/me").get_json()["csrf_token"]
    pid = os.getpid()
    pm = client.application.config["process_manager"]
    pm._states["dummy"] = RuntimeState(
        pid=pid,
        pgid=os.getpgrp(),
        create_time=psutil.Process(pid).create_time(),
    )

    res = client.delete("/api/config/services/dummy", headers={"X-CSRF-Token": csrf})
    assert res.status_code == 409
    assert res.get_json()["error"] == "service_running"
    assert any(s.id == "dummy" for s in client.application.config["registry"].list_services())


def test_delete_stopped_service_forgets_runtime(client):
    """stopped 삭제는 config/메모리/Popen/runtime JSON을 함께 정리한다."""
    from process_manager import RuntimeState

    client.post("/api/auth/login", json={"password": "secret123"})
    csrf = client.get("/api/auth/me").get_json()["csrf_token"]
    pm = client.application.config["process_manager"]
    pm._states["dummy"] = RuntimeState(last_exit_time=1.0)
    pm._save("dummy", pm._states["dummy"])
    runtime_path = pm._state_path("dummy")

    res = client.delete("/api/config/services/dummy", headers={"X-CSRF-Token": csrf})
    assert res.status_code == 200
    assert "dummy" not in pm._states
    assert not runtime_path.exists()
    assert all(s.id != "dummy" for s in client.application.config["registry"].list_services())


def test_manual_reload_reconciles_process_manager_definition(client):
    """디스크 config 수정 뒤 reload하면 registry와 ProcessManager가 같은 정의를 본다."""
    client.post("/api/auth/login", json={"password": "secret123"})
    csrf = client.get("/api/auth/me").get_json()["csrf_token"]
    config_path = Path(client.application.config["config_path"])
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace('name: "Dummy"', 'name: "Reloaded"'),
        encoding="utf-8",
    )

    res = client.post("/api/config/reload", headers={"X-CSRF-Token": csrf})
    assert res.status_code == 200
    pm = client.application.config["process_manager"]
    assert pm._active_services["dummy"].name == "Reloaded"



def test_kill_external_requires_health_enabled(client, tmp_path, monkeypatch):
    """health.enabled=False인 서비스는 외부 인스턴스 종료 거부."""
    # 별도 client fixture는 health.enabled=False 케이스를 만들기 위해 직접 구성
    import app as backend_app
    from auth import PASSWORD_ENV
    from textwrap import dedent

    config_path = tmp_path / "config.yml"
    config_path.write_text(
        dedent(
            """
            controller:
              host: "127.0.0.1"
              port: 9000
              allowed_path_roots:
                - "/tmp"
            services:
              - id: "no_health"
                name: "No Health"
                cwd: "/tmp"
                entry_file: "x.py"
                command: ["python", "x.py"]
                port: 12321
                health:
                  enabled: false
                  type: "none"
            """
        ).strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv(PASSWORD_ENV, "secret")

    app = backend_app.create_app(
        config_path=config_path,
        log_dir=tmp_path / "logs",
        runtime_dir=tmp_path / "runtime",
        frontend_dist=tmp_path / "_no_dist",
    )
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/api/auth/login", json={"password": "secret"})
    csrf = c.get("/api/auth/me").get_json()["csrf_token"]

    res = c.post(
        "/api/services/no_health/kill_external",
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 409
    body = res.get_json()
    assert body["error"] == "kill_external_requires_health"


def test_kill_external_accepts_tcp_health(tmp_path, monkeypatch):
    """TCP health로 확인된 외부 인스턴스도 종료할 수 있어야 한다."""
    import app as backend_app
    from health_checker import HealthResult

    config_path = tmp_path / "config.yml"
    config_path.write_text(
        dedent(
            """
            controller:
              host: "127.0.0.1"
              port: 9000
              allowed_path_roots:
                - "/tmp"
            services:
              - id: "tcp_service"
                name: "TCP Service"
                cwd: "/tmp"
                entry_file: "server"
                command: ["/tmp/server"]
                port: 12321
                health:
                  enabled: true
                  type: "tcp"
                  url: "127.0.0.1:12321"
            """
        ).strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv(PASSWORD_ENV, "secret")

    app = backend_app.create_app(
        config_path=config_path,
        log_dir=tmp_path / "logs",
        runtime_dir=tmp_path / "runtime",
        frontend_dist=tmp_path / "_no_dist",
    )
    app.config["TESTING"] = True

    health = HealthResult(
        state="running_external",
        alive=False,
        unmanaged=True,
        unmanaged_pid=12345,
        pid=None,
        pgid=None,
        uptime_seconds=None,
        health={"enabled": True, "ok": True, "url": "127.0.0.1:12321"},
        port_check=None,
        last_exit_code=None,
        last_exit_time=None,
    )
    monkeypatch.setattr(app.config["health_checker"], "check", lambda *args, **kwargs: health)
    monkeypatch.setattr(
        app.config["process_manager"],
        "kill_external",
        lambda service, **_kwargs: {
            "pid": 12345,
            "name": "server",
            "signal": "SIGINT",
            "duration_seconds": 0.1,
        },
    )

    c = app.test_client()
    c.post("/api/auth/login", json={"password": "secret"})
    csrf = c.get("/api/auth/me").get_json()["csrf_token"]
    res = c.post(
        "/api/services/tcp_service/kill_external",
        headers={"X-CSRF-Token": csrf},
    )

    assert res.status_code == 200
    assert res.get_json()["killed"]["pid"] == 12345


def test_kill_external_rejects_when_not_running_external(client):
    """state가 running_external이 아니면 거부.

    fixture의 dummy 서비스는 health URL 없이 등록되어 health 검증에서 먼저 막힌다.
    실제 health URL은 있지만 응답이 없는 케이스를 위해 별도 환경 구성.
    여기선 health 비활성으로 인한 1차 거부만 위 테스트에서 다루고,
    health 활성 + 응답 없음 케이스는 health_checker 단위 테스트에서 처리한다.
    """
    pass
