"""Config writes, reconciliation, rollback and response share one transaction."""
from contextlib import nullcontext
from threading import Event, RLock, Thread, current_thread
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import json
from flask import Flask

from auth import PASSWORD_ENV, init_app
from config_checkpoint import ConfigCheckpoint
from config_loader import load_config
from file_browser import FileBrowser
from process_manager import ProcessError
from routes import auth_api, config_api
from service_registry import ServiceRegistry


def _service(sid="one", name="Original"):
    return {"id": sid, "name": name, "cwd": "/tmp", "entry_file": "x.py", "command": ["python", "x.py"]}


def _group(gid="group", name="Original"):
    return {"id": gid, "name": name, "items": [{"id": "echo", "name": "Echo", "kind": "argv", "command": ["echo", "hello"]}]}


@pytest.fixture
def app(tmp_path, monkeypatch):
    path = tmp_path / "config.yml"
    path.write_text(json.dumps({
        "controller": {"host": "127.0.0.1", "port": 9000, "allowed_path_roots": ["/tmp"]},
        "services": [_service(), _service("two")],
        "actions": [_group(), _group("other")],
    }))
    cfg = load_config(path)
    checkpoint = ConfigCheckpoint(tmp_path / "runtime")
    checkpoint.save_from(path)
    monkeypatch.setenv(PASSWORD_ENV, "test-password")
    app = Flask(__name__)
    app.config.update(TESTING=True, config_path=path, registry=ServiceRegistry(cfg),
                      config_checkpoint=checkpoint,
                      file_browser=FileBrowser(cfg.controller.allowed_path_roots),
                      resource_sampler=SimpleNamespace(prune=Mock()),
                      process_manager=SimpleNamespace(
                          service_operation=lambda sid: nullcontext(),
                          reconcile_service_definitions=Mock(),
                          inspect_state=lambda sid: (SimpleNamespace(pid=None), False),
                          forget_service=Mock()))
    init_app(app, secret_key_path=tmp_path / "secret")
    app.register_blueprint(auth_api.bp)
    app.register_blueprint(config_api.bp)
    return app


def _client(app):
    client = app.test_client()
    login = client.post("/api/auth/login", json={"password": "test-password"})
    return client, {"X-CSRF-Token": login.get_json()["csrf_token"]}


_MUTATIONS = [
    ("post", "/api/config/actions", _group("new", "Saved"), "upsert_action_group"),
    ("put", "/api/config/actions/group", _group(name="Saved"), "upsert_action_group"),
    ("delete", "/api/config/actions/group", None, "delete_action_group"),
    ("post", "/api/config/actions/reorder", {"order": ["other", "group"]}, "reorder_action_groups"),
    ("post", "/api/config/services/reorder", {"order": ["two", "one"]}, "reorder_services"),
]


@pytest.mark.parametrize("method,url,payload,writer_name", _MUTATIONS)
def test_rejected_service_edit_cannot_rollback_concurrent_write(app, monkeypatch, method, url, payload, writer_name):
    reconcile_entered, release_reconcile = Event(), Event()
    second_attempted, second_wrote = Event(), Event()
    real_lock = RLock()

    class ObservedLock:
        def __enter__(self):
            if current_thread().name == "second-writer":
                second_attempted.set()
            real_lock.acquire()

        def __exit__(self, *args):
            real_lock.release()

    monkeypatch.setattr(config_api, "_config_reconcile_lock", ObservedLock())
    writer = getattr(config_api, writer_name)

    def observed_writer(*args, **kwargs):
        second_wrote.set()
        return writer(*args, **kwargs)

    monkeypatch.setattr(config_api, writer_name, observed_writer)

    def reconcile(services):
        if any(service.name == "Rejected" for service in services):
            reconcile_entered.set()
            assert release_reconcile.wait(5)
            raise ProcessError("running service rejects changed definition")

    app.config["process_manager"].reconcile_service_definitions = reconcile
    first_client, first_headers = _client(app)
    second_client, second_headers = _client(app)
    responses, errors = {}, []

    def send(key, client, verb, target, body, headers):
        try:
            responses[key] = client.open(target, method=verb, json=body, headers=headers)
        except Exception as exc:
            errors.append(exc)

    first = Thread(target=send, args=("first", first_client, "PUT", "/api/config/services/one", _service(name="Rejected"), first_headers))
    second = Thread(name="second-writer", target=send, args=("second", second_client, method.upper(), url, payload, second_headers))
    first.start()
    try:
        assert reconcile_entered.wait(3)
        second.start()
        assert second_attempted.wait(3)
        assert not second_wrote.is_set()
    finally:
        release_reconcile.set()
        first.join(5)
        if second.ident is not None:
            second.join(5)
    assert not first.is_alive() and not second.is_alive()
    assert not errors
    assert responses["first"].status_code == 409
    assert responses["second"].status_code == 200
    saved = load_config(app.config["config_path"])
    registry = app.config["registry"]
    assert saved.raw == app.config["config_checkpoint"].load().raw == registry.config.raw
    assert next(s for s in saved.services if s.id == "one").name == "Original"
    if url.endswith("/reorder"):
        entries = saved.services if "services" in url else saved.action_groups
        assert [entry.id for entry in entries] == payload["order"]
    elif method == "delete":
        assert "group" not in [g.id for g in saved.action_groups]
    else:
        assert registry.get_action_group(payload["id"]).name == "Saved"
        assert responses["second"].get_json()["group"]["name"] == "Saved"


@pytest.mark.parametrize("method,url,payload", [
    *[(method, url, payload) for method, url, payload, _ in _MUTATIONS],
    ("post", "/api/config/services", _service("new")),
    ("put", "/api/config/services/one", _service(name="Changed")),
    ("delete", "/api/config/services/one", None),
])
def test_reconcile_failure_is_409_and_restores_config(app, method, url, payload):
    original = app.config["config_path"].read_bytes()
    app.config["process_manager"].reconcile_service_definitions = Mock(side_effect=ProcessError("blocked"))
    client, headers = _client(app)
    response = client.open(url, method=method.upper(), json=payload, headers=headers)
    assert response.status_code == 409
    assert response.get_json()["error"] == "config_reload_blocked"
    assert app.config["config_path"].read_bytes() == original
