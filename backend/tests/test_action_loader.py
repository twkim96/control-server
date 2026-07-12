"""ActionGroup config 파싱과 deny list 검증 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from config_loader import (
    ConfigError,
    load_config,
    validate_command_safety,
)


def _write_yaml(tmp_path: Path, body: str) -> Path:
    cfg = tmp_path / "config.yml"
    cfg.write_text(body, encoding="utf-8")
    return cfg


def _base_yaml(actions_yaml: str = "") -> str:
    return (
        "controller:\n"
        "  host: '127.0.0.1'\n"
        "  port: 8765\n"
        "  editable_config: true\n"
        "  allowed_path_roots:\n"
        "    - '/Users/example/Documents'\n"
        "  auth:\n"
        "    type: 'password'\n"
        "    password_env: 'CONTROL_PASSWORD'\n"
        "services: []\n"
        f"{actions_yaml}"
    )


def test_actions_section_optional(tmp_path: Path) -> None:
    cfg = _write_yaml(tmp_path, _base_yaml())
    app_config = load_config(cfg)
    assert app_config.action_groups == ()


def test_action_group_basic(tmp_path: Path) -> None:
    body = _base_yaml(
        "actions:\n"
        "  - id: 'example_action'\n"
        "    name: 'Example Action'\n"
        "    items:\n"
        "      - id: 'scanner'\n"
        "        name: 'Scanner'\n"
        "        kind: 'python'\n"
        "        cwd: '/Users/example/Documents'\n"
        "        command: ['python', '-u', 'scanner.py']\n"
    )
    cfg = _write_yaml(tmp_path, body)
    app_config = load_config(cfg)
    assert len(app_config.action_groups) == 1
    group = app_config.action_groups[0]
    assert group.id == "example_action"
    assert len(group.items) == 1
    item = group.items[0]
    assert item.kind == "python"
    assert item.command == ("python", "-u", "scanner.py")
    assert item.log.keep_runs == 20


def test_action_group_argv_kind(tmp_path: Path) -> None:
    body = _base_yaml(
        "actions:\n"
        "  - id: 'disk'\n"
        "    name: 'Disk'\n"
        "    items:\n"
        "      - id: 'example_command'\n"
        "        name: 'Example Command'\n"
        "        kind: 'argv'\n"
        "        command: ['example-tool', 'subcommand', '--option']\n"
    )
    cfg = _write_yaml(tmp_path, body)
    app_config = load_config(cfg)
    item = app_config.action_groups[0].items[0]
    assert item.kind == "argv"
    assert item.cwd is None


def test_python_kind_requires_cwd(tmp_path: Path) -> None:
    body = _base_yaml(
        "actions:\n"
        "  - id: 'g'\n"
        "    name: 'g'\n"
        "    items:\n"
        "      - id: 'a'\n"
        "        name: 'a'\n"
        "        kind: 'python'\n"
        "        command: ['python', '-u', 'a.py']\n"
    )
    cfg = _write_yaml(tmp_path, body)
    with pytest.raises(ConfigError, match="cwd"):
        load_config(cfg)


def test_invalid_kind_rejected(tmp_path: Path) -> None:
    body = _base_yaml(
        "actions:\n"
        "  - id: 'g'\n"
        "    name: 'g'\n"
        "    items:\n"
        "      - id: 'a'\n"
        "        name: 'a'\n"
        "        kind: 'shell'\n"
        "        command: ['echo']\n"
    )
    cfg = _write_yaml(tmp_path, body)
    with pytest.raises(ConfigError, match="kind"):
        load_config(cfg)


def test_duplicate_group_id_rejected(tmp_path: Path) -> None:
    body = _base_yaml(
        "actions:\n"
        "  - id: 'g'\n"
        "    name: 'g'\n"
        "    items:\n"
        "      - id: 'a'\n"
        "        name: 'a'\n"
        "        kind: 'argv'\n"
        "        command: ['ls']\n"
        "  - id: 'g'\n"
        "    name: 'g2'\n"
        "    items:\n"
        "      - id: 'b'\n"
        "        name: 'b'\n"
        "        kind: 'argv'\n"
        "        command: ['ls']\n"
    )
    cfg = _write_yaml(tmp_path, body)
    with pytest.raises(ConfigError, match="중복"):
        load_config(cfg)


def test_duplicate_item_id_rejected(tmp_path: Path) -> None:
    body = _base_yaml(
        "actions:\n"
        "  - id: 'g'\n"
        "    name: 'g'\n"
        "    items:\n"
        "      - id: 'x'\n"
        "        name: 'x'\n"
        "        kind: 'argv'\n"
        "        command: ['ls']\n"
        "      - id: 'x'\n"
        "        name: 'x2'\n"
        "        kind: 'argv'\n"
        "        command: ['pwd']\n"
    )
    cfg = _write_yaml(tmp_path, body)
    with pytest.raises(ConfigError, match="중복"):
        load_config(cfg)


@pytest.mark.parametrize(
    "command",
    [
        ["sudo", "ls"],
        ["rm", "-rf", "/"],
        ["/bin/rm", "x"],
        ["/usr/bin/sudo", "ls"],
        ["mv", "a", "b"],
        ["chmod", "777", "x"],
        ["kill", "-9", "1"],
    ],
)
def test_deny_list_blocks_argv(command: list[str]) -> None:
    with pytest.raises(ConfigError):
        validate_command_safety(tuple(command), kind="argv", where="t")


@pytest.mark.parametrize(
    "command",
    [
        ["bash"],
        ["sh", "-c", "echo hi"],
        ["/bin/zsh"],
        ["python", "-c", "print(1)"],
        ["/usr/bin/python3", "-c", "1"],
        ["ruby", "-e", "puts 1"],
        ["node", "-e", "1"],
    ],
)
def test_shell_and_eval_blocked_for_argv(command: list[str]) -> None:
    with pytest.raises(ConfigError):
        validate_command_safety(tuple(command), kind="argv", where="t")


def test_python_kind_allows_python_script() -> None:
    # python kind도 인터프리터 + -u + 스크립트는 정상 사용. 차단되면 안 됨.
    validate_command_safety(
        ("python", "-u", "scanner.py"),
        kind="python",
        where="t",
    )


def test_python_kind_still_blocks_sudo() -> None:
    with pytest.raises(ConfigError):
        validate_command_safety(("sudo", "x"), kind="python", where="t")


def test_python_kind_blocks_eval() -> None:
    # GPT 크로스체크 반영: kind=python이 deny list 우회로가 되지 않도록
    # 두 kind 모두에 EVAL 인터프리터 차단을 적용.
    with pytest.raises(ConfigError):
        validate_command_safety(
            ("python", "-c", "print(1)"),
            kind="python",
            where="t",
        )


def test_python_kind_blocks_shell() -> None:
    with pytest.raises(ConfigError):
        validate_command_safety(("bash", "-c", "x"), kind="python", where="t")


def test_external_logs_parsed(tmp_path: Path) -> None:
    body = _base_yaml(
        "actions:\n"
        "  - id: 'g'\n"
        "    name: 'g'\n"
        "    items:\n"
        "      - id: 'a'\n"
        "        name: 'a'\n"
        "        kind: 'argv'\n"
        "        command: ['ls']\n"
        "        external_logs:\n"
        "          - name: 'success'\n"
        "            path: '/tmp/success.log'\n"
        "          - name: 'fail'\n"
        "            path: '/tmp/fail.log'\n"
    )
    cfg = _write_yaml(tmp_path, body)
    app_config = load_config(cfg)
    item = app_config.action_groups[0].items[0]
    assert len(item.external_logs) == 2
    assert item.external_logs[0].name == "success"
    assert item.external_logs[1].path == "/tmp/fail.log"
