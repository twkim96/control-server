import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {acquireInheritedLock} from '../../lib/transaction.mjs';

test('Python fallback retains lock after helper exits, excludes another open, and releases on parent close', () => {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'cs-flock-'));
  const name=path.join(root,'lock');
  const one=fs.openSync(name,'w+',0o600);
  const two=fs.openSync(name,'r+');
  const options={lockf:path.join(root,'missing-lockf')};
  try {
    assert.equal(acquireInheritedLock(one,options).status,0);
    assert.equal(acquireInheritedLock(two,options).status,75);
    fs.closeSync(one);
    assert.equal(acquireInheritedLock(two,options).status,0);
  } finally {
    try {fs.closeSync(one);} catch {}
    fs.closeSync(two);
    fs.rmSync(root,{recursive:true,force:true});
  }
});
