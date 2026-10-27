import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import test from 'node:test';

test('runtime probe exposes process fields without inherited environment', async () => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'cs-probe-'));
  try {
    await fs.mkdir(path.join(dir, 'bin'));
    await fs.writeFile(path.join(dir, 'pm2.pid'), '12345');
    await fs.writeFile(path.join(dir, 'index.js'), `module.exports={
      connect: cb=>cb(), disconnect: ()=>{},
      list: cb=>cb(null,[{name:'server-control--example',pid:42,pm2_env:{status:'online',restart_time:3,pm_uptime:123,env:{SECRET:'never-output'},SECRET:'never-output'}},{name:'other',pm2_env:{env:{SECRET:'never-output'}}}])
    };`);
    const result = spawnSync(process.execPath, ['lib/pm2-probe.mjs', path.join(dir, 'bin/pm2'), 'runtime'], {
      encoding:'utf8', env: {...process.env, PM2_HOME:dir},
    });
    assert.equal(result.status, 0, result.stderr);
    const data = JSON.parse(result.stdout);
    assert.equal(data.length, 2);
    assert.match(data[1].control_pm2_daemon_id, /^12345:[0-9]+(?:\.[0-9]+)?$/);
    assert.equal(data[0].pid, 42);
    assert.equal(data[0].pm2_env.restart_time, 3);
    assert.equal(result.stdout.includes('never-output'), false);
    assert.equal('env' in data[0].pm2_env, false);
  } finally { await fs.rm(dir, {recursive:true,force:true}); }
});
