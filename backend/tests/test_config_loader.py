"""config_loader 단위 테스트.

공개 `backend/config.example.yml` 로드 + 주요 오류 케이스를 검증한다.
"""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from config_loader import ConfigError, load_config


BACKEND = Path(__file__).resolve().parent.parent
EXAMPLE_CONFIG = BACKEND / "config.example.yml"


def test_example_config_loads() -> None:
    cfg = load_config(EXAMPLE_CONFIG)
    assert cfg.controller.port == 9000
    assert cfg.controller.host == "127.0.0.1"
    assert cfg.services == ()
    assert cfg.action_groups == ()


def test_example_config_controller_shape() -> None:
    cfg = load_config(EXAMPLE_CONFIG)
    assert cfg.controller.editable_config is True
    assert cfg.controller.allowed_path_roots == ("/path/to/projects",)
    assert cfg.controller.auth_type == "password"
    assert cfg.controller.auth_password_env == "CONTROL_PASSWORD"


def _write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "config.yml"
    p.write_text(dedent(content).strip(), encoding="utf-8")
    return p


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="찾을 수 없습니다"):
        load_config(tmp_path / "no_such.yml")


def test_invalid_action_type(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        controller:
          host: "0.0.0.0"
          port: 9000
        services:
          - id: "x"
            name: "x"
            cwd: "/tmp"
            entry_file: "x.py"
            command: ["python", "x.py"]
            actions:
              - id: "weird"
                label: "weird"
                type: "bogus"
        """,
    )
    with pytest.raises(ConfigError, match="type은"):
        load_config(p)


def test_duplicate_service_id(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        controller:
          host: "0.0.0.0"
          port: 9000
        services:
          - id: "x"
            name: "x"
            cwd: "/tmp"
            entry_file: "x.py"
            command: ["python", "x.py"]
          - id: "x"
            name: "x2"
            cwd: "/tmp"
            entry_file: "x.py"
            command: ["python", "x.py"]
        """,
    )
    with pytest.raises(ConfigError, match="서비스 ID 중복"):
        load_config(p)


@pytest.mark.parametrize("service_id", ["한글", "_hidden", "-hidden", ".hidden"])
def test_service_id_rejects_values_pm2_cannot_namespace(
    tmp_path: Path,
    service_id: str,
) -> None:
    p = _write(
        tmp_path,
        f"""
        controller:
          host: "0.0.0.0"
          port: 9000
        services:
          - id: "{service_id}"
            name: "x"
            cwd: "/tmp"
            entry_file: "x.py"
            command: ["python", "x.py"]
        """,
    )
    with pytest.raises(ConfigError, match="영문/숫자로 시작"):
        load_config(p)


def test_service_id_accepts_pm2_safe_dot(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        controller:
          host: "0.0.0.0"
          port: 9000
        services:
          - id: "safe.service_1"
            name: "x"
            cwd: "/tmp"
            entry_file: "x.py"
            command: ["python", "x.py"]
        """,
    )
    assert load_config(p).services[0].id == "safe.service_1"


def test_duplicate_action_id(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        controller:
          host: "0.0.0.0"
          port: 9000
        services:
          - id: "x"
            name: "x"
            cwd: "/tmp"
            entry_file: "x.py"
            command: ["python", "x.py"]
            actions:
              - id: "start"
                label: "시작"
                type: "process_start"
              - id: "start"
                label: "다시"
                type: "process_restart"
        """,
    )
    with pytest.raises(ConfigError, match="action id 중복"):
        load_config(p)


def test_invalid_lifecycle_mode(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        controller:
          host: "0.0.0.0"
          port: 9000
        services:
          - id: "x"
            name: "x"
            cwd: "/tmp"
            entry_file: "x.py"
            command: ["python", "x.py"]
            lifecycle:
              mode: "weird"
        """,
    )
    with pytest.raises(ConfigError, match="lifecycle.mode"):
        load_config(p)


def test_missing_lifecycle_defaults_to_external_adoption(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        controller:
          host: "0.0.0.0"
          port: 9000
        services:
          - id: "x"
            name: "x"
            cwd: "/tmp"
            entry_file: "x.py"
            command: ["python", "x.py"]
        """,
    )
    assert load_config(p).services[0].lifecycle.unmanaged_policy == "manage"


def test_missing_unmanaged_policy_defaults_to_external_adoption(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        controller:
          host: "0.0.0.0"
          port: 9000
        services:
          - id: "x"
            name: "x"
            cwd: "/tmp"
            entry_file: "x.py"
            command: ["python", "x.py"]
            lifecycle:
              mode: "manual"
        """,
    )
    assert load_config(p).services[0].lifecycle.unmanaged_policy == "manage"


def test_command_must_be_non_empty_list(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        controller:
          host: "0.0.0.0"
          port: 9000
        services:
          - id: "x"
            name: "x"
            cwd: "/tmp"
            entry_file: "x.py"
            command: []
        """,
    )
    with pytest.raises(ConfigError, match="command"):
        load_config(p)


def test_adopt_command_is_optional_and_parsed(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        controller:
          host: "0.0.0.0"
          port: 9000
        services:
          - id: "x"
            name: "x"
            cwd: "/tmp"
            entry_file: "run.sh"
            command: ["/tmp/run.sh"]
            adopt_command: ["/tmp/bin/server", "--serve"]
        """,
    )
    service = load_config(p).services[0]
    assert service.command == ("/tmp/run.sh",)
    assert service.adopt_command == ("/tmp/bin/server", "--serve")


def test_adopt_command_must_be_non_empty_list(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        controller:
          host: "0.0.0.0"
          port: 9000
        services:
          - id: "x"
            name: "x"
            cwd: "/tmp"
            entry_file: "run.sh"
            command: ["/tmp/run.sh"]
            adopt_command: []
        """,
    )
    with pytest.raises(ConfigError, match="adopt_command"):
        load_config(p)


def test_adopt_match_prefix_requires_explicit_multi_token_command(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        controller: {host: "127.0.0.1", port: 9000}
        services:
          - id: x
            name: x
            cwd: /tmp
            entry_file: x.py
            command: [python, x.py]
            adopt_command: [server, start]
            adopt_match: prefix
        """,
    )
    assert load_config(p).services[0].adopt_match == "prefix"
    p.write_text(p.read_text().replace("[server, start]", "[server]"), encoding="utf-8")
    with pytest.raises(ConfigError, match="2개 이상"):
        load_config(p)


def test_health_http_requires_url(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        controller:
          host: "0.0.0.0"
          port: 9000
        services:
          - id: "x"
            name: "x"
            cwd: "/tmp"
            entry_file: "x.py"
            command: ["python", "x.py"]
            health:
              enabled: true
              type: "http"
        """,
    )
    with pytest.raises(ConfigError, match="health.type=http"):
        load_config(p)


def test_health_tcp_requires_url(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        controller:
          host: "0.0.0.0"
          port: 9000
        services:
          - id: "x"
            name: "x"
            cwd: "/tmp"
            entry_file: "x.py"
            command: ["python", "x.py"]
            health:
              enabled: true
              type: "tcp"
        """,
    )
    with pytest.raises(ConfigError, match="health.type=tcp"):
        load_config(p)


def test_https_config_parses_env_names(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        controller:
          host: "0.0.0.0"
          port: 9000
        services:
          - id: "x"
            name: "x"
            cwd: "/tmp"
            entry_file: "x.py"
            command: ["python", "x.py"]
            https:
              enabled: true
              cert_file: ".certs/dev.crt"
              key_file: ".certs/dev.key"
              env:
                enabled: "VMA_DEV_HTTPS"
                cert_file: "VMA_DEV_CERT_FILE"
                key_file: "VMA_DEV_KEY_FILE"
        """,
    )
    cfg = load_config(p)
    service = cfg.services[0]
    assert service.https.enabled is True
    assert service.https.cert_file == ".certs/dev.crt"
    assert service.https.key_file == ".certs/dev.key"
    assert service.https.enabled_env_name == "VMA_DEV_HTTPS"
    assert service.https.cert_file_env_name == "VMA_DEV_CERT_FILE"
    assert service.https.key_file_env_name == "VMA_DEV_KEY_FILE"


def test_https_env_names_must_be_safe(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        controller:
          host: "0.0.0.0"
          port: 9000
        services:
          - id: "x"
            name: "x"
            cwd: "/tmp"
            entry_file: "x.py"
            command: ["python", "x.py"]
            https:
              enabled: true
              env:
                enabled: "BAD-NAME"
        """,
    )
    with pytest.raises(ConfigError, match="https.env.enabled"):
        load_config(p)


def test_invalid_service_id_chars(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        controller:
          host: "0.0.0.0"
          port: 9000
        services:
          - id: "bad id!"
            name: "x"
            cwd: "/tmp"
            entry_file: "x.py"
            command: ["python", "x.py"]
        """,
    )
    with pytest.raises(ConfigError, match="id는 영문"):
        load_config(p)


@pytest.mark.parametrize(
    ("service_port", "health", "message"),
    [
        (0, "", "port는 1..65535"),
        (70000, "", "port는 1..65535"),
        ("true", "", "port는 정수"),
        (None, "health:\n              enabled: true\n              type: none", "http 또는 tcp"),
        (None, "health:\n              enabled: true\n              type: http\n              url: http://127.0.0.1\n              timeout_seconds: 0", "0초 초과"),
    ],
)
def test_rejects_invalid_port_or_enabled_health(tmp_path, service_port, health, message):
    port_line = "" if service_port is None else f"port: {service_port}"
    p = _write(
        tmp_path,
        f"""
        controller:
          host: "127.0.0.1"
          port: 9000
        services:
          - id: x
            name: x
            cwd: /tmp
            entry_file: x.py
            command: [python, x.py]
            {port_line}
            {health}
        """,
    )
    with pytest.raises(ConfigError, match=message):
        load_config(p)


def test_rejects_duplicate_and_controller_colliding_service_ports(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        controller: {host: "127.0.0.1", port: 9000}
        services:
          - {id: one, name: one, cwd: /tmp, entry_file: one.py, command: [python, one.py], port: 9001}
          - {id: two, name: two, cwd: /tmp, entry_file: two.py, command: [python, two.py], port: 9001}
        """,
    )
    with pytest.raises(ConfigError, match="port 중복"):
        load_config(p)

    p.write_text(p.read_text().replace("port: 9001}", "port: 9000}", 1), encoding="utf-8")
    with pytest.raises(ConfigError, match="controller.port"):
        load_config(p)
