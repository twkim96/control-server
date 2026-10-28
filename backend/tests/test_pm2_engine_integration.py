"""Opt-in real engine upgrade; uses only an isolated short PM2_HOME and fixture ports."""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from config_loader import load_config
from pm2_manager import Pm2Manager

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PM2_ENGINE_INTEGRATION") != "1",
    reason="set RUN_PM2_ENGINE_INTEGRATION=1 for isolated engine upgrade with npm download",
)


def _port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_real_engine_upgrade_preserves_service_states_and_is_idempotent(monkeypatch):
    repo = Path(__file__).resolve().parents[2]
    root = Path(tempfile.mkdtemp(prefix="cs-eng-", dir="/private/tmp"))
    runtime = root / "runtime"
    node = shutil.which("node")
    assert node
    pinned = repo / "ops/pm2/node_modules/pm2/bin/pm2"
    monkeypatch.setenv("CONTROL_PM2_NODE", node)
    monkeypatch.setenv("CONTROL_PM2_CLI", str(pinned))
    monkeypatch.setenv("CONTROL_PYTHON", sys.executable)
    monkeypatch.delenv("CONTROL_PM2_ENGINE_CLI", raising=False)
    monkeypatch.delenv("CONTROL_PM2_ENGINE_TOKEN", raising=False)
    listener = root / "listener.py"
    listener.write_text("import http.server,sys\nhttp.server.HTTPServer(('127.0.0.1', int(sys.argv[1])), http.server.SimpleHTTPRequestHandler).serve_forever()\n")
    ports = [_port(), _port()]
    while ports[0] == ports[1]:
        ports[1] = _port()
    raw = {"controller": {"host": "127.0.0.1", "port": _port()}, "services": []}
    for sid, port in zip(["engine_running", "engine_stopped"], ports):
        raw["services"].append({
            "id": sid, "name": sid, "cwd": str(root), "entry_file": "listener.py",
            "command": [sys.executable, "-u", str(listener), str(port)], "port": port,
            "health": {"enabled": True, "type": "tcp", "url": f"127.0.0.1:{port}", "timeout_seconds": 1},
        })
    config_path = root / "config.yml"
    config_path.write_text(json.dumps(raw))
    config = load_config(config_path)
    manager = Pm2Manager(runtime, root / "logs", command_timeout_seconds=25)
    env = {**os.environ, "CONTROL_PM2_HOME": str(runtime / "pm2")}
    try:
        stopped = config.services[1]
        manager.start(stopped)
        manager.stop(stopped)
        manager.start(config.services[0])
        command = [node, str(repo / "lib/pm2-engine.mjs"), "update", str(runtime), str(repo), str(config_path)]
        result = subprocess.run(command, env=env, capture_output=True, timeout=300, check=False)
        job = json.loads((runtime / "pm2-engine/job.json").read_text())
        assert result.returncode == 0, {k: job.get(k) for k in ["phase", "message", "error", "rolled_back"]}
        assert job["status"] == "succeeded"
        assert job["health_checked"] == 1
        probe = subprocess.run([str(repo / "scripts/pm2ctl.sh"), "__engine_snapshot"], env=env, capture_output=True, text=True, timeout=30, check=True)
        state = json.loads(probe.stdout.strip().splitlines()[-1])
        assert state["version"] == job["target_version"]
        running = next(p for p in state["processes"] if p["name"] == "server-control--engine_running")
        stopped_state = next(p for p in state["processes"] if p["name"] == "server-control--engine_stopped")
        assert running["status"] == "online" and running["pid"] > 0
        assert stopped_state["status"] in {"stopped", "errored"} and stopped_state["pid"] == 0
        result = subprocess.run(command, env=env, capture_output=True, timeout=45, check=False)
        assert result.returncode == 0
        manager.invalidate()
        after = manager.snapshot(force=True)
        assert after["engine_running"].pid == running["pid"]
        print(f"isolated PM2 {job['from_version']} -> {job['target_version']}; running=1 stopped=1; health=1; repeated update kept PID")
    finally:
        manager.shutdown()
        # Cleanup only this test's daemon, even if a failed recovery fence remains.
        current = runtime / "pm2-engine/current/node_modules/pm2/bin/pm2"
        subprocess.run([node, str(current if current.is_file() else pinned), "kill"],
                       env={**env, "PM2_HOME": str(runtime / "pm2")},
                       capture_output=True, timeout=45, check=False)
        deadline = time.monotonic() + 5
        pid_file = runtime / "pm2/pm2.pid"
        while pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not pid_file.exists(), "isolated daemon cleanup did not complete"
        shutil.rmtree(root)


@pytest.mark.parametrize("scenario", ["upgrade", "forced-rollback", "interrupted-empty", "interrupted-online", "restart", "restart-rollback"])
def test_real_engine_never_executes_inactive_apps(scenario):
    rollback = scenario not in {"upgrade", "restart"}
    """Count launches throughout cutover, not just final stopped/errored status."""
    repo = Path(__file__).resolve().parents[2]
    previous = Path(os.environ.get("CONTROL_TEST_PM2_PREVIOUS_CLI", repo / "ops/pm2/node_modules/pm2/bin/pm2")).resolve()
    candidate = Path(os.environ.get("CONTROL_TEST_PM2_CANDIDATE_CLI", repo / "ops/pm2/node_modules/pm2/bin/pm2")).resolve()
    if scenario.startswith("restart"):
        candidate = previous
    previous_version = json.loads((previous.parents[1] / "package.json").read_text())["version"]
    target_version = json.loads((candidate.parents[1] / "package.json").read_text())["version"]
    if previous_version == target_version and not scenario.startswith("restart"):
        pytest.skip("supply distinct installed CONTROL_TEST_PM2_PREVIOUS_CLI and CONTROL_TEST_PM2_CANDIDATE_CLI")
    node = shutil.which("node")
    assert node
    root = Path(tempfile.mkdtemp(prefix="cs-noexec-", dir="/private/tmp"))
    runtime = root / "runtime"
    env = {**os.environ, "CONTROL_PM2_NODE": node, "CONTROL_PM2_CLI": str(previous),
           "CONTROL_PYTHON": sys.executable, "CONTROL_PM2_HOME": str(runtime / "pm2"),
           "CONTROL_PM2_ENGINE_CLI": "", "CONTROL_PM2_ENGINE_TOKEN": ""}
    wrapper = str(repo / "scripts/pm2ctl.sh")

    def pm2(*args):
        return subprocess.run([wrapper, *args], env=env, text=True, capture_output=True, timeout=60, check=True)

    def snapshot():
        return json.loads(pm2("__engine_snapshot").stdout.strip().splitlines()[-1])

    def counts():
        return {sid: (root / f"{sid}.count").read_text().count("launch\n") for sid in ["stopped", "errored"]}

    app = root / "app.cjs"
    app.write_text("const fs=require('fs');fs.appendFileSync(process.argv[2],'launch\\n');"
                   "if(process.argv[3]==='errored')process.exit(1);else setInterval(()=>{},1000);\n")
    try:
        for sid in ["running", "stopped", "errored"]:
            manifest = root / f"{sid}.json"
            manifest.write_text(json.dumps({"apps": [{
                "name": f"server-control--{sid}", "script": str(app), "interpreter": node,
                "args": [str(root / f"{sid}.count"), sid], "cwd": str(root),
                "min_uptime": 10000, "max_restarts": 1, "autorestart": True,
                "out_file": str(root / "out.log"), "error_file": str(root / "err.log"),
            }]}))
            pm2("start", str(manifest))
            deadline = time.monotonic() + 10
            while not (root / f"{sid}.count").exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert (root / f"{sid}.count").exists()
            if sid == "stopped":
                pm2("stop", f"server-control--{sid}")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            before = snapshot()
            if any(p["name"] == "server-control--errored" and p["status"] == "errored" and not p["pid"] for p in before["processes"]):
                break
            time.sleep(0.1)
        else:
            pytest.fail("fixture did not enter errored state")
        backup = root / "backup.pm2"
        if scenario.startswith("interrupted"):
            pm2("save", "--force")
            shutil.copyfile(runtime / "pm2/dump.pm2", backup)
            if scenario == "interrupted-empty":
                pm2("kill")
                assert snapshot()["processes"] == []
            else:
                pm2("restart", "server-control--stopped")
                deadline = time.monotonic() + 5
                while counts()["stopped"] < 2 and time.monotonic() < deadline:
                    time.sleep(0.05)
                assert counts()["stopped"] == 2
        baseline = counts()
        daemon_before = int((runtime / "pm2/pm2.pid").read_text())
        # Reuse installed packages, with an injected post-cutover health failure
        # for rollback. Every PM2 operation itself uses the real daemon and CLI.
        driver = root / "update.mjs"
        driver.write_text("""
import fs from 'node:fs/promises';
import path from 'node:path';
const [modulePath,runtimeDir,appRoot,candidate,target,rollback,scenario,backup,previous,beforeJSON] = process.argv.slice(2);
if(scenario.startsWith("interrupted")){
  await fs.mkdir(path.join(runtimeDir,"pm2-engine"),{recursive:true});
  await fs.writeFile(path.join(runtimeDir,"pm2-engine/recovery.json"), JSON.stringify({
    before:JSON.parse(beforeJSON),previous_cli:previous,previous_version:JSON.parse(await fs.readFile(path.resolve(previous,"../../package.json"),"utf8")).version,
    previous_target:null,dump:backup,health_targets:[]
  }));
}
const {updateEngine,restartEngine,runCaptured,packageVersion} = await import(modulePath);
let candidateActive=false,resurrects=0;
const operate=scenario.startsWith("restart")?restartEngine:updateEngine;
const result=await operate({runtimeDir,appRoot,configPath:'unused',hooks:{
  latest:async()=>target,
  install:async(prefix)=>{await fs.mkdir(path.join(prefix,'node_modules'),{recursive:true});
    await fs.symlink(path.resolve(candidate,'../..'),path.join(prefix,'node_modules/pm2'));},
  execute:async(command,args,options)=>{const output=await runCaptured(command,args,options);
    if(args[0]==='resurrect'){resurrects++;candidateActive=scenario.startsWith('restart')?resurrects===1:packageVersion(options.env.CONTROL_PM2_ENGINE_CLI)===target;}
    return output;},
  healthTargets:async()=>[{name:'server-control--running'}],
  probeHealth:async()=>rollback!=='true'||!candidateActive,
  verifyTimeout:0,pollInterval:0,
}});
process.stdout.write(JSON.stringify({status:result.status,rolled_back:result.rolled_back})+'\\n');
""")
        result = subprocess.run([node, str(driver), str(repo / "lib/pm2-engine.mjs"), str(runtime),
                                 str(repo), str(candidate), target_version, str(rollback).lower(), scenario, str(backup), str(previous), json.dumps(before)],
                                env=env, capture_output=True, text=True, timeout=180, check=True)
        job = json.loads(result.stdout.strip().splitlines()[-1])
        assert job["status"] == ("failed" if rollback else "succeeded")
        if rollback:
            assert job["rolled_back"] is True
        after = snapshot()
        assert after["version"] == (previous_version if rollback else target_version)
        assert counts() == baseline, "inactive app executed during engine update/restart/rollback"
        if scenario.startswith("restart"):
            assert int((runtime / "pm2/pm2.pid").read_text()) != daemon_before
        for item in after["processes"]:
            if item["name"] == "server-control--running":
                assert item["status"] == "online" and item["pid"] > 0
            else:
                assert item["status"] == "stopped" and item["pid"] == 0
        if scenario == "interrupted-empty":
            config_path = root / "config.json"
            config_path.write_text(json.dumps({"controller": {"host": "127.0.0.1", "port": _port()}, "services": [{
                "id": "stopped", "name": "stopped", "cwd": str(root), "entry_file": "app.cjs",
                "command": [node, str(app), str(root / "stopped.count"), "stopped"],
            }]}))
            config = load_config(config_path)
            monkeypatch_env = {key: os.environ.get(key) for key in env if key.startswith("CONTROL_")}
            try:
                os.environ.update({key: value for key, value in env.items() if key.startswith("CONTROL_")})
                manager = Pm2Manager(runtime, root / "logs", command_timeout_seconds=25)
                try:
                    manager.start(config.services[0])
                    deadline = time.monotonic() + 5
                    while counts()["stopped"] == baseline["stopped"] and time.monotonic() < deadline:
                        time.sleep(0.05)
                    assert counts()["stopped"] == baseline["stopped"] + 1
                    manager.stop(config.services[0])
                finally:
                    manager.shutdown()
            finally:
                for key, value in monkeypatch_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
        assert not (runtime / "pm2-engine/recovery.json").exists()
        assert not (runtime / "pm2-engine/maintenance-owner").exists()
        print(f"isolated {previous_version}->{target_version}, scenario={scenario}: inactive launch counts unchanged {baseline}")
    finally:
        current = runtime / "pm2-engine/current/node_modules/pm2/bin/pm2"
        subprocess.run([node, str(current if current.is_file() else previous), "kill"],
                       env={**env, "PM2_HOME": str(runtime / "pm2")}, capture_output=True, timeout=45, check=False)
        deadline = time.monotonic() + 5
        while (runtime / "pm2/pm2.pid").exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not (runtime / "pm2/pm2.pid").exists(), "isolated daemon cleanup did not complete"
        shutil.rmtree(root)
