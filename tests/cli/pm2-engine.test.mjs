import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { engineLayout, updateEngine, statesMatch, validateSnapshot, packageVersion, runCaptured, writeSafeRestoreDump } from "../../lib/pm2-engine.mjs";
import { withFileLock, withManagedLock, atomicWriteJson } from "../../lib/transaction.mjs";
import { parseArgs } from "../../lib/cli.mjs";

async function fixture(callback) {
  const root = await fs.promises.mkdtemp("/private/tmp/cs-engine-test-");
  const appRoot = path.join(root, "app");
  const runtimeDir = path.join(root, "runtime");
  const layout = engineLayout(runtimeDir);
  const writePackage = async (prefix, version) => {
    const dir = path.join(prefix, "node_modules/pm2");
    await fs.promises.mkdir(path.join(dir, "bin"), { recursive: true });
    await fs.promises.writeFile(path.join(dir, "bin/pm2"), "// fixture");
    await atomicWriteJson(path.join(dir, "package.json"), { version });
  };
  await writePackage(path.join(appRoot, "ops/pm2"), "7.0.3");
  await fs.promises.mkdir(layout.pm2Home, { recursive: true });
  let state = { version: "7.0.3", processes: [
    { name: "server-control--running", status: "online", pid: 101 },
    { name: "server-control--stopped", status: "stopped", pid: 0 },
  ] };
  const commands = [];
  let applyCandidate = true;
  let failRecovery = false;
  let candidateStopsRunning = false;
  const hooks = {
    latest: async () => "7.0.4",
    install: writePackage,
    snapshot: async () => structuredClone(state),
    healthTargets: async () => [],
    verifyTimeout: 0,
    pollInterval: 0,
    execute: async (_command, args, options) => {
      const version = packageVersion(options.env.CONTROL_PM2_ENGINE_CLI);
      commands.push({ version, args });
      if (args[0] === "save") await fs.promises.writeFile(path.join(layout.pm2Home, "dump.pm2"), JSON.stringify(state.processes));
      if (args[0] === "kill") state.processes = [];
      if (args[0] === "resurrect") {
        const saved = JSON.parse(await fs.promises.readFile(path.join(layout.pm2Home, "dump.pm2")));
        state.processes = saved.map(item => ({...item, pid:item.status === "online" ? 303 : 0}));
        if (version === "7.0.3" && failRecovery) throw new Error("simulated old engine failure");
        if (version !== "7.0.4" || applyCandidate) state.version = version;
        if (version === "7.0.4") {

          if (candidateStopsRunning) state.processes[0] = { ...state.processes[0], status: "stopped", pid: 0 };
        }
      }
      if (args[0] === "stop") {
        const item = state.processes.find((item) => item.name === args[1]);
        item.status = "stopped"; item.pid = 0;
      }
      if (args[0] === "restart") {
        const item = state.processes.find((item) => item.name === args[1]);
        item.status = "online"; item.pid = 303;
      }
      return "";
    },
  };
  try {
    await callback({ root, layout, appRoot, runtimeDir, hooks, commands,
      options: { appRoot, runtimeDir, configPath: path.join(root, "config.yml"), hooks },
      state: () => state,
      setState: (next) => { state = next; },
      noCandidateVersion: () => { applyCandidate = false; },
      stopRunningDuringUpdate: () => { candidateStopsRunning = true; },
      failRollback: () => { failRecovery = true; },
    });
  } finally { await fs.promises.rm(root, { recursive: true, force: true }); }
}

test("engine update verifies daemon version and preserves running/stopped service intent", async () => fixture(async (f) => {
  const before = structuredClone(f.state());
  const result = await updateEngine(f.options);
  assert.equal(result.status, "succeeded");
  assert.equal(f.state().version, "7.0.4");
  assert.equal(statesMatch(before, f.state()), true);
  assert.equal(fs.realpathSync(f.layout.current), path.join(f.layout.releases, "7.0.4"));
  assert.equal(fs.existsSync(f.layout.recovery), false);
  assert.equal(fs.existsSync(f.layout.owner), false);
  assert.ok(f.commands.some(({ args }) => args[0] === "kill"));
  assert.ok(f.commands.every(({ args }) => args[0] !== "update"));
}));

test("installation failure leaves the running daemon and active pointer untouched", async () => fixture(async (f) => {
  f.hooks.install = async () => { throw new Error("simulated network failure"); };
  const result = await updateEngine(f.options);
  assert.equal(result.status, "failed");
  assert.equal(f.state().version, "7.0.3");
  assert.deepEqual(f.commands, []);
  assert.equal(fs.existsSync(f.layout.current), false);
}));

test("CLI exit success with wrong daemon version rolls back running intent from the saved dump", async () => fixture(async (f) => {
  const before = structuredClone(f.state());
  f.noCandidateVersion();
  f.stopRunningDuringUpdate();
  const result = await updateEngine(f.options);
  assert.equal(result.status, "failed");
  assert.equal(result.rolled_back, true);
  assert.equal(f.state().version, "7.0.3");
  assert.equal(statesMatch(before, f.state()), true);
  assert.ok(f.commands.some(({ version, args }) => version === "7.0.3" && args[0] === "resurrect"));
  assert.equal(fs.existsSync(f.layout.recovery), false);
}));

test("failed recovery keeps the fence and journal, and next attempt performs recovery before another upgrade", async () => fixture(async (f) => {
  f.noCandidateVersion(); f.failRollback();
  const result = await updateEngine(f.options);
  assert.equal(result.rolled_back, false);
  assert.equal(fs.existsSync(f.layout.recovery), true);
  const oldExecute = f.hooks.execute;
  f.hooks.execute = async (command, args, options) => {
    if (args[0] === "resurrect" && packageVersion(options.env.CONTROL_PM2_ENGINE_CLI) === "7.0.3") {
      await oldExecute(command, args, options).catch(() => {});
      f.state().version = "7.0.3"; return "";
    }
    return oldExecute(command, args, options);
  };
  f.hooks.latest = () => { throw new Error("must not check latest before recovery"); };
  const retried = await updateEngine(f.options);
  assert.equal(retried.rolled_back, true);
  assert.equal(fs.existsSync(f.layout.recovery), false);
}));

test("unknown or transitional processes abort before package or daemon mutations", async () => fixture(async (f) => {
  f.state().processes.push({ name: "someone-else", status: "online", pid: 400 });
  const result = await updateEngine(f.options);
  assert.equal(result.status, "failed");
  assert.deepEqual(f.commands, []);
  assert.throws(() => validateSnapshot({ processes: [{ name: "server-control--valid", status: "stopping", pid: 12 }] }), /상태/);
}));

test("daemon drift without a matching rollback binary is refused before cutover", async () => fixture(async (f) => {
  f.state().version = "7.0.2";
  const result = await updateEngine(f.options);
  assert.equal(result.status, "failed");
  assert.match(result.error, /복구용 설치본/);
  assert.deepEqual(f.commands, []);
}));

test("concurrent attempt cannot overwrite another running job status", async () => fixture(async (f) => {
  await fs.promises.mkdir(f.layout.root, { mode: 0o700 });
  const active = { id: "another-job", pid: process.pid, status: "running" };
  await atomicWriteJson(f.layout.job, active);
  await withManagedLock({ home: f.layout.home }, async () => {
    const result = await updateEngine(f.options);
    assert.equal(result.status, "failed");
    assert.deepEqual(JSON.parse(fs.readFileSync(f.layout.job)), active);
  });
}));

test("prehealthy services must recover before the new engine is committed", async () => fixture(async (f) => {
  f.hooks.healthTargets = async () => [{ name: "server-control--running", type: "tcp", port: 12345 }];
  f.hooks.probeHealth = async () => f.state().version !== "7.0.4";
  const result = await updateEngine(f.options);
  assert.equal(result.rolled_back, true);
  assert.equal(fs.existsSync(f.layout.current), false);
}));

test("nested engine CLI commands parse without changing install argument defaults", () => {
  assert.equal(parseArgs(["pm2", "check"]).command, "pm2-check");
  assert.equal(parseArgs(["pm2", "update", "--home", "/private/tmp/control"]).home, "/private/tmp/control");
});

test("ordinary PM2 calls wait for another poll but reject an active engine cutover", async () => fixture(async (f) => {
  await fs.promises.mkdir(f.layout.root, { mode: 0o700 });
  const cli = path.join(f.appRoot, "ops/pm2/node_modules/pm2/bin/pm2");
  await fs.promises.writeFile(cli, 'process.stdout.write("fixture-ready\\n");');
  const command = () => runCaptured(fileURLToPath(new URL("../../scripts/pm2ctl.sh", import.meta.url)), ["help"], {
    env: { ...process.env, CONTROL_PM2_HOME: f.layout.pm2Home, CONTROL_PM2_NODE: process.execPath, CONTROL_PM2_CLI: cli, CONTROL_PM2_ENGINE_TOKEN: "", CONTROL_PM2_ENGINE_CLI: "" },
    timeout: 15_000,
  });
  let pending;
  await withFileLock(f.layout.maintenanceLock, async () => {
    pending = command().then((output) => ({ output }), (error) => ({ error }));
    await new Promise((resolve) => setTimeout(resolve, 200));
  });
  const result = await pending;
  assert.ifError(result.error);
  assert.equal(result.output, "fixture-ready\n");
  await fs.promises.writeFile(f.layout.owner, "fixture-owner\n", { mode: 0o600 });
  await withFileLock(f.layout.maintenanceLock, async () => {
    await assert.rejects(command(), /exit 75/);
  });
}));

test("non-running dump entries are non-executable before resurrect, including legacy journals", async () => fixture(async (f) => {
  const source = path.join(f.root, "old.pm2"), target = path.join(f.root, "safe.pm2");
  const before = {processes:[
    {name:"server-control--running",status:"online",pid:101},
    {name:"server-control--stopped",status:"stopped",pid:0},
    {name:"server-control--errored",status:"errored",pid:0},
  ]};
  await atomicWriteJson(source, before.processes.map(item => ({...item,instances:1,autostart:true,_tree_pids:[999]})));
  await writeSafeRestoreDump(source,target,before);
  const restored = JSON.parse(fs.readFileSync(target));
  assert.equal(restored[0].status,"online");
  for (const item of restored.slice(1)) {
    assert.equal(item.status,"stopped");
    assert.equal(item.autostart,false);
    assert.equal("instances" in item,false);
    assert.equal("_tree_pids" in item,false);
  }
  assert.equal(JSON.parse(fs.readFileSync(source))[2].status,"errored");
  assert.equal(fs.statSync(target).mode & 0o777,0o600);
}));

test("malformed or mismatched saved membership is refused before restore mutations", async () => fixture(async (f) => {
  const source=path.join(f.root,"bad.pm2"),target=path.join(f.root,"safe.pm2");
  for (const payload of [{processes:[]},[],[null],[{name:"server-control--unexpected"}]]) {
    await atomicWriteJson(source,payload);
    await assert.rejects(writeSafeRestoreDump(source,target,f.state()));
    assert.equal(fs.existsSync(target),false);
  }
}));

test("errored app is stopped before engine update and before rollback update", async () => fixture(async (f) => {
  f.state().processes[1].status="errored";
  f.noCandidateVersion();
  const execute=f.hooks.execute;
  f.hooks.execute=async (command,args,options) => {
    if (args[0]==="resurrect") {
      assert.ok(f.state().processes.every(item=>item.status!=="errored"));
    }
    return execute(command,args,options);
  };
  const result=await updateEngine(f.options);
  assert.equal(result.rolled_back,true);
  const firstUpdate=f.commands.findIndex(command=>command.args[0]==="resurrect");
  const stopped=f.commands.findIndex(command=>command.args[0]==="stop");
  assert.ok(stopped>=0 && stopped<firstUpdate);
}));

test("changed expected target version is refused before installation or daemon mutation", async () => fixture(async (f) => {
  f.hooks.install = () => { throw new Error("must not install"); };
  const result = await updateEngine({...f.options, expectedVersion:"7.0.3"});
  assert.equal(result.status,"failed");
  assert.match(result.error,/목표 버전/);
  assert.deepEqual(f.commands,[]);
}));

test("unsupported execution triggers are refused before daemon cutover", async () => fixture(async (f) => {
  for (const options of [{cron_restart:"* * * * *"},{watch:true},{instances:2},{exec_mode:"cluster_mode"},{pmx_module:true}]) {
    f.state().processes[0].options=options;
    const result = await updateEngine(f.options);
    assert.equal(result.status,"failed");
    assert.deepEqual(f.commands,[]);
  }
}));
