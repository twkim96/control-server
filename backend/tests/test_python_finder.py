"""python_finder 단위 테스트.

실제 시스템 경로를 건드리지 않도록 monkeypatch + tmp_path 기반.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import python_finder
from python_finder import (
    PythonInterpreter,
    list_python_interpreters,
)


def _make_executable(path: Path, version: str) -> None:
    """`python -V`가 `Python <version>`을 stderr로 출력하는 가짜 인터프리터."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\n"
        f"echo 'Python {version}' >&2\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_lists_interpreters_from_path(tmp_path: Path, monkeypatch) -> None:
    bin_dir = tmp_path / "bin"
    _make_executable(bin_dir / "python3", "3.11.4")
    _make_executable(bin_dir / "python3.12", "3.12.1")

    # 고정 디렉터리는 비워두고, PATH에만 가짜 디렉터리 둠
    monkeypatch.setattr(python_finder, "_FIXED_DIRS", ())
    monkeypatch.setattr(python_finder, "_HOME_DIRS", ())
    monkeypatch.setattr(python_finder, "_PYENV_VERSIONS_GLOB", str(tmp_path / "no_versions" / "*"))
    monkeypatch.setattr(python_finder, "_PYENV_SHIMS_GLOB", str(tmp_path / "no_shims" / "*"))
    monkeypatch.setenv("PATH", str(bin_dir))

    items = list_python_interpreters()
    paths = [i.path for i in items]
    versions = {i.path: i.version for i in items}

    assert str(bin_dir / "python3") in paths
    assert str(bin_dir / "python3.12") in paths
    assert versions[str(bin_dir / "python3")] == "3.11.4"
    assert versions[str(bin_dir / "python3.12")] == "3.12.1"


def test_dedup_by_realpath(tmp_path: Path, monkeypatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    real = bin_dir / "python3.11"
    _make_executable(real, "3.11.4")
    # 같은 바이너리에 대한 symlink (python3, python)
    (bin_dir / "python3").symlink_to(real)
    (bin_dir / "python").symlink_to(real)

    monkeypatch.setattr(python_finder, "_FIXED_DIRS", ())
    monkeypatch.setattr(python_finder, "_HOME_DIRS", ())
    monkeypatch.setattr(python_finder, "_PYENV_VERSIONS_GLOB", str(tmp_path / "no_versions" / "*"))
    monkeypatch.setattr(python_finder, "_PYENV_SHIMS_GLOB", str(tmp_path / "no_shims" / "*"))
    monkeypatch.setenv("PATH", str(bin_dir))

    items = list_python_interpreters()
    # 같은 realpath면 한 번만 등장.
    assert len(items) == 1


def test_broken_binary_excluded(tmp_path: Path, monkeypatch) -> None:
    bin_dir = tmp_path / "bin"
    good = bin_dir / "python3"
    bad = bin_dir / "python3.99"
    _make_executable(good, "3.11.4")
    bad.parent.mkdir(parents=True, exist_ok=True)
    # 실행은 되지만 -V 출력에 'Python <version>' 패턴이 없음 → exclude
    bad.write_text("#!/bin/sh\necho 'I am not Python' >&2\n", encoding="utf-8")
    bad.chmod(0o755)

    monkeypatch.setattr(python_finder, "_FIXED_DIRS", ())
    monkeypatch.setattr(python_finder, "_HOME_DIRS", ())
    monkeypatch.setattr(python_finder, "_PYENV_VERSIONS_GLOB", str(tmp_path / "no_versions" / "*"))
    monkeypatch.setattr(python_finder, "_PYENV_SHIMS_GLOB", str(tmp_path / "no_shims" / "*"))
    monkeypatch.setenv("PATH", str(bin_dir))

    items = list_python_interpreters()
    paths = [i.path for i in items]
    assert str(good) in paths
    assert str(bad) not in paths


def test_pyenv_shim_gets_note_and_sorted_last(tmp_path: Path, monkeypatch) -> None:
    bin_dir = tmp_path / "regular"
    shims_dir = tmp_path / ".pyenv" / "shims"
    versions_dir = tmp_path / ".pyenv" / "versions" / "3.11.4" / "bin"

    _make_executable(bin_dir / "python3", "3.11.4")
    _make_executable(shims_dir / "python", "3.11.4")
    _make_executable(versions_dir / "python", "3.11.4")

    monkeypatch.setattr(python_finder, "_FIXED_DIRS", ())
    monkeypatch.setattr(python_finder, "_HOME_DIRS", ())
    monkeypatch.setattr(
        python_finder,
        "_PYENV_VERSIONS_GLOB",
        str(tmp_path / ".pyenv" / "versions" / "*" / "bin" / "python"),
    )
    monkeypatch.setattr(
        python_finder,
        "_PYENV_SHIMS_GLOB",
        str(shims_dir / "python*"),
    )
    monkeypatch.setenv("PATH", os.pathsep.join([str(bin_dir), str(shims_dir)]))

    items = list_python_interpreters()
    # shim entry는 마지막
    assert items[-1].path == str(shims_dir / "python")
    assert "shim" in items[-1].note
    # version dir는 note에 'pyenv version'
    version_paths = [i for i in items if "pyenv version" in i.note]
    assert any(str(versions_dir / "python") == i.path for i in version_paths)


def test_returns_empty_when_nothing_found(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(python_finder, "_FIXED_DIRS", ())
    monkeypatch.setattr(python_finder, "_HOME_DIRS", ())
    monkeypatch.setattr(python_finder, "_PYENV_VERSIONS_GLOB", str(tmp_path / "no_versions" / "*"))
    monkeypatch.setattr(python_finder, "_PYENV_SHIMS_GLOB", str(tmp_path / "no_shims" / "*"))
    monkeypatch.setenv("PATH", str(tmp_path / "empty_dir_does_not_exist"))

    assert list_python_interpreters() == []


def test_to_dict_round_trip() -> None:
    interp = PythonInterpreter(path="/x/python3", version="3.11.0", note="hint")
    d = interp.to_dict()
    assert d == {"path": "/x/python3", "version": "3.11.0", "note": "hint"}


# ----------------------------------------------------------------------
# 라우트 통합
# ----------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
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
            services: []
            """
        ).strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv(PASSWORD_ENV, "pw")

    app = backend_app.create_app(
        config_path=config_path,
        log_dir=tmp_path / "logs",
        runtime_dir=tmp_path / "runtime",
        frontend_dist=tmp_path / "_no_dist",
    )
    app.config["TESTING"] = True
    return app.test_client()


def test_route_requires_auth(client):
    res = client.get("/api/system/python_interpreters")
    assert res.status_code == 401


def test_controller_resource_route_requires_auth(client):
    res = client.get("/api/system/controller_resource")
    assert res.status_code == 401


def test_route_returns_interpreters_after_login(client, monkeypatch, tmp_path):
    # 결과를 결정적으로 만들기 위해 가짜 환경 주입
    monkeypatch.setattr(python_finder, "_FIXED_DIRS", ())
    monkeypatch.setattr(python_finder, "_HOME_DIRS", ())
    monkeypatch.setattr(python_finder, "_PYENV_VERSIONS_GLOB", str(tmp_path / "no_versions" / "*"))
    monkeypatch.setattr(python_finder, "_PYENV_SHIMS_GLOB", str(tmp_path / "no_shims" / "*"))
    bin_dir = tmp_path / "bin"
    _make_executable(bin_dir / "python3", "3.11.4")
    monkeypatch.setenv("PATH", str(bin_dir))

    client.post("/api/auth/login", json={"password": "pw"})
    res = client.get("/api/system/python_interpreters")
    assert res.status_code == 200
    body = res.get_json()
    assert "interpreters" in body
    assert any(i["path"] == str(bin_dir / "python3") for i in body["interpreters"])


def test_controller_resource_route_returns_current_process_resource(client):
    client.post("/api/auth/login", json={"password": "pw"})

    res = client.get("/api/system/controller_resource")
    assert res.status_code == 200

    resource = res.get_json()["resource"]
    assert resource["available"] is True
    assert resource["reason"] == "ok"
    assert isinstance(resource["pid"], int)
    assert isinstance(resource["sampled_at"], float)
    assert resource["cpu_percent"] is None or isinstance(resource["cpu_percent"], float)
    assert isinstance(resource["memory_rss_bytes"], int)
    assert resource["memory_rss_bytes"] > 0
    assert resource["process_count"] == 1
    assert resource["children_count"] == 0


def test_controller_resource_route_ignores_stale_controller_state(client):
    from process_manager import RuntimeState

    client.post("/api/auth/login", json={"password": "pw"})
    client.application.config["controller_runtime_state"] = RuntimeState(
        pid=os.getpid(),
        create_time=0.0,
    )

    res = client.get("/api/system/controller_resource")
    assert res.status_code == 200

    resource = res.get_json()["resource"]
    assert resource["available"] is True
    assert resource["reason"] == "ok"
    assert resource["pid"] == os.getpid()
    assert isinstance(resource["memory_rss_bytes"], int)
    assert resource["memory_rss_bytes"] > 0
