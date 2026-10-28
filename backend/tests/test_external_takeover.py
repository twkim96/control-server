"""Takeover replaces one verified lifetime; no unmanaged fallback or repeated kills."""
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace

import pytest

from config_schema import RecoveryConfig
from service_operations import OperationsError, ServiceOperations, _definition_id
from backend.tests.test_service_operations import daemon_operations, service


@pytest.fixture
def takeover(tmp_path, monkeypatch):
    s = replace(service(), port=12345)
    ops, pm, health, clock = daemon_operations(tmp_path, [s])
    candidate = SimpleNamespace(pid=99, create_time=1004.0)
    pm.external_takeover_operation = nullcontext
    pm.build_manifest = lambda s: {'apps': []}
    pm.snapshot = lambda **_: dict(pm.processes)
    pm.evaluate_external_identity = lambda _: SimpleNamespace(diagnostics=SimpleNamespace(ok=True), candidate=candidate)
    def kill(s, **expected):
        assert expected == {'expected_pid':99, 'expected_create_time':1004.0, 'expected_daemon_id':pm.boot}
        assert ops._entry(s.id)['desired_state'] == 'stopped'
        pm.calls.append(('kill', s.id))
        return {'pid':99}
    pm.kill_external = kill
    health.check = lambda s, **_: SimpleNamespace(state='running' if pm.get_process(s.id) else 'running_external',
        alive=bool(pm.get_process(s.id)),unmanaged=not bool(pm.get_process(s.id)),health={'ok':True})
    monkeypatch.setattr('service_operations.psutil.Process', lambda _: SimpleNamespace(uids=lambda: SimpleNamespace(real=__import__('os').getuid())))
    monkeypatch.setattr('process_manager._verify_destructive_identity', lambda *_: None)
    monkeypatch.setattr('net_utils.find_port_holder', lambda _: {'pid':99})
    return SimpleNamespace(s=s,ops=ops,pm=pm,health=health,clock=clock,candidate=candidate)


def run(f):
    return f.ops.take_over_external(f.s,pid=99,created_at=1004.0)


def test_manual_takeover_records_stopped_before_kill_and_running_before_start(takeover):
    f=takeover
    start=f.pm.start
    def checked(s):
        assert f.ops._entry(s.id)['desired_state']=='running'
        return start(s)
    f.pm.start=checked
    assert run(f)=={'pid':99}
    assert f.pm.calls==[('kill','one'),('start','one')]
    assert f.ops._entry('one')['desired_state']=='running'
    assert [e['type'] for e in f.ops.events('one')['events']]==['external_takeover_requested','start','external_takeover_succeeded']


@pytest.mark.parametrize('case', ['birth','pid','health','uid','managed','missing_daemon','start_disabled','definition','maintenance','intent_write','manifest'])
def test_unsafe_preflight_never_signals_or_starts(takeover, monkeypatch, case):
    f=takeover
    if case=='birth': f.candidate.create_time+=0.001
    if case=='pid': f.candidate.pid=100
    if case=='health': f.health.check=lambda *a,**k:SimpleNamespace(state='unhealthy')
    if case=='uid': monkeypatch.setattr('service_operations.psutil.Process',lambda _:SimpleNamespace(uids=lambda:SimpleNamespace(real=-1)))
    if case=='managed': f.pm.put('one',online=False)
    if case=='missing_daemon': f.pm.boot=None
    if case=='start_disabled': f.s=replace(f.s,actions=());f.ops.registry.services=(f.s,)
    if case=='definition': f.ops.registry.services=(replace(f.s,name='changed'),)
    if case=='maintenance':
        p=f.ops.runtime/'pm2-engine/maintenance-owner';p.parent.mkdir();p.touch()
    if case=='intent_write': monkeypatch.setattr(f.ops,'_persist',lambda: (_ for _ in ()).throw(OSError('disk full')))
    if case=='manifest': f.pm.build_manifest=lambda _: (_ for _ in ()).throw(RuntimeError('bad manifest'))
    with pytest.raises((OperationsError,RuntimeError)):
        run(f)
    assert f.pm.calls==[]


@pytest.mark.parametrize('stage', ['kill','start','readiness'])
def test_failure_never_reports_success_or_spawns_unmanaged(takeover, stage):
    f=takeover
    if stage=='kill': f.pm.kill_external=lambda *a,**k: (_ for _ in ()).throw(RuntimeError('private output'))
    if stage=='start': f.pm.fail_start.add('one')
    if stage=='readiness': f.ops.READY_SECONDS=0
    with pytest.raises(OperationsError,match={'kill':'external_termination_failed','start':'pm2_start_failed','readiness':'readiness_failed'}[stage]):
        run(f)
    types=[e['type'] for e in f.ops.events('one')['events']]
    assert types[-1]=='external_takeover_failed'
    assert 'external_takeover_succeeded' not in types
    if stage=='kill': assert f.pm.calls==[] and f.ops._entry('one')['desired_state']=='stopped'


def bootstrap(f):
    f.s=replace(f.s,recovery=RecoveryConfig(auto_takeover=True));f.ops.registry.services=(f.s,)
    f.pm.put('one');f.ops.poll_once()
    f.pm.processes.clear();f.pm.boot='102:1005.0';f.clock.advance()


def test_auto_takeover_only_after_replacement_with_prior_owned_running_intent(takeover):
    f=takeover;bootstrap(f);f.ops.poll_once()
    assert f.pm.calls==[('kill','one'),('start','one')]
    assert f.ops.recovery_status('one')['status']=='restored'
    assert f.ops._entry('one')['daemon_restore_attempts']==3
    f.clock.advance();f.ops.poll_once()
    assert f.pm.calls==[('kill','one'),('start','one')]


@pytest.mark.parametrize('case',['off','stopped','never_owned','old_orphan','new_unrelated','expired','same_daemon','changed_definition','disabled','unhealthy','retried'])
def test_auto_takeover_does_not_kill_unqualified_external(takeover,case):
    f=takeover;bootstrap(f)
    if case=='off': f.s=replace(f.s,recovery=RecoveryConfig());f.ops.registry.services=(f.s,)
    if case=='stopped': f.ops.prepare_intent(f.s,'stop')
    if case=='never_owned': f.ops._entry('one').pop('owned_pid')
    if case=='old_orphan': f.candidate.create_time=100
    if case=='new_unrelated': f.candidate.create_time=1006
    if case=='expired': f.clock.advance(400)
    if case=='same_daemon': f.pm.boot='101:1'
    if case=='changed_definition': f.s=replace(f.s,name='changed');f.ops.registry.services=(f.s,)
    if case=='disabled': f.ops._automatic_enabled=False
    if case=='unhealthy': f.health.check=lambda *a,**k:SimpleNamespace(state='unhealthy')
    if case=='retried':
        f.ops._entry('one').update(daemon_id=f.pm.boot,daemon_restore_status='blocked',daemon_restore_attempts=3)
    f.ops.poll_once()
    assert f.pm.calls==[]


def test_default_policy_field_does_not_invalidate_prepatch_definition_hash(takeover):
    import hashlib,json
    from dataclasses import asdict
    d=asdict(takeover.s);d['recovery'].pop('auto_takeover')
    assert _definition_id(takeover.s)==hashlib.sha256(json.dumps(d,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def test_takeover_api_requires_auth_csrf_and_immutable_identity(tmp_path, monkeypatch):
    import app as backend_app
    from backend.tests.test_pm2_api import FakePm2, _config, _login
    monkeypatch.setenv('CONTROL_PASSWORD','secret')
    config=tmp_path/'config.yml';_config(config,tmp_path)
    app=backend_app.create_app(config_path=config,runtime_dir=tmp_path/'runtime',log_dir=tmp_path/'logs',
        frontend_dist=tmp_path/'dist',process_backend='pm2',pm2_runner=FakePm2())
    client=app.test_client();url='/api/services/dummy/take_over'
    assert client.post(url).status_code==401
    csrf=_login(client);headers={'X-CSRF-Token':csrf}
    assert client.post(url,json={}).status_code==403
    assert client.post(url,headers=headers).status_code==400
    for body in [{},{'pid':True,'created_at':1},{'pid':99,'created_at':False},{'pid':99,'created_at':float('nan')}]:
        response=client.post(url,json=body,headers=headers)
        assert response.status_code==409
        assert response.json['error']=='external_identity_required'
    received=[]
    def execute(s,**kw):
        received.append((s.id,kw));return {'pid':99}
    app.config['service_operations'].take_over_external=execute
    response=client.post(url,json={'pid':99,'created_at':1004.0},headers=headers)
    assert response.status_code==200
    assert response.json['replaced']=={'pid':99}
    assert received==[('dummy',{'pid':99,'created_at':1004.0})]
    app.config['process_backend']='native'
    assert client.post(url,json={'pid':99,'created_at':1004.0},headers=headers).status_code==409


@pytest.mark.skipif(__import__('os').environ.get('RUN_PM2_INTEGRATION')!='1',reason='isolated real PM2 takeover opt-in')
def test_real_external_to_pm2_takeover_proves_exit_single_listener_and_health(monkeypatch):
    import os,sys,subprocess,tempfile,shutil,time
    from pathlib import Path
    import psutil
    from backend.tests.test_pm2_integration import _service,_free_port,_wait_port
    from config_schema import ActionConfig,HealthConfig
    from log_manager import LogManager
    from process_manager import ProcessManager
    from pm2_manager import Pm2Manager
    from health_checker import HealthChecker
    from backend.tests.test_service_operations import Registry
    root=Path(tempfile.mkdtemp(prefix='cs-take-',dir='/private/tmp'));runtime=root/'runtime'
    listener=root/'listener.py';listener.write_text('import http.server,sys\nhttp.server.HTTPServer(("127.0.0.1",int(sys.argv[1])),http.server.SimpleHTTPRequestHandler).serve_forever()\n')
    node=shutil.which('node');assert node
    cli=Path(os.environ.get('CONTROL_TEST_PM2_CANDIDATE_CLI',Path(__file__).resolve().parents[2]/'ops/pm2/node_modules/pm2/bin/pm2'))
    monkeypatch.setenv('CONTROL_PM2_NODE',node);monkeypatch.setenv('CONTROL_PM2_CLI',str(cli));monkeypatch.setenv('CONTROL_PYTHON',sys.executable)
    monkeypatch.delenv('CONTROL_PM2_ENGINE_CLI',raising=False);monkeypatch.delenv('CONTROL_PM2_ENGINE_TOKEN',raising=False)
    port=_free_port();s=replace(_service(root,port),health=HealthConfig(True,'tcp',f'127.0.0.1:{port}',1,True),actions=(ActionConfig('start','Start','process_start',True),))
    helper=ProcessManager(runtime,LogManager(root/'logs'));pm=Pm2Manager(runtime,root/'logs',external_helper=helper)
    health=HealthChecker(pm);ops=ServiceOperations(Registry([s]),pm,health,runtime)
    external=None
    try:
        external=subprocess.Popen(list(s.command),cwd=s.cwd,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        _wait_port(port,True)
        born=psutil.Process(external.pid).create_time()
        pm.snapshot(force=True)  # Warm one authoritative snapshot before health readers.
        assert health.check(s,force_health_probe=True).state=='running_external'
        # A stale browser identity cannot terminate a replacement lifetime.
        with pytest.raises(OperationsError,match='external_identity_changed'):
            ops.take_over_external(s,pid=external.pid,created_at=born-0.001)
        assert external.poll() is None
        killed=ops.take_over_external(s,pid=external.pid,created_at=born)
        external.wait(timeout=5)
        assert killed['pid']==external.pid
        managed=pm.get_process(s.id,force=True)
        assert managed.alive and managed.pid!=external.pid
        assert health.check(s,force_health_probe=True).state=='running'
        assert psutil.Process(managed.pid).is_running()
        assert ops.events(s.id)['events'][-1]['type']=='external_takeover_succeeded'
        assert not (runtime/'pm2-engine/maintenance-owner').exists()
    finally:
        ops.shutdown();health.close();pm.shutdown();helper.shutdown()
        if external and external.poll() is None:
            external.terminate();external.wait(timeout=5)
        subprocess.run([node,str(cli),'kill'],env={**os.environ,'PM2_HOME':str(runtime/'pm2')},capture_output=True,timeout=30)
        deadline=time.monotonic()+5
        while (runtime/'pm2/pm2.pid').exists() and time.monotonic()<deadline:time.sleep(0.1)
        assert not (runtime/'pm2/pm2.pid').exists()
        shutil.rmtree(root)


def test_managed_home_lock_excludes_node_engine_or_app_transaction(tmp_path):
    import subprocess,shutil
    from pathlib import Path
    from file_lock import managed_lock
    node=shutil.which('node')
    if not node: pytest.skip('Node required for cross-language lock validation')
    module=Path(__file__).resolve().parents[2]/'lib/transaction.mjs'
    script=f'''import {{withManagedLock}} from {__import__('json').dumps(module.as_uri())};
    try{{await withManagedLock({{home:process.argv[1]}},async()=>{{}});process.exitCode=0;}}
    catch(e){{process.exitCode=e.code==='CONTROL_LOCK_BUSY'?75:1;}}'''
    with managed_lock(tmp_path):
        result=subprocess.run([node,'--input-type=module','-e',script,str(tmp_path)],capture_output=True,timeout=10)
        assert result.returncode==75
    result=subprocess.run([node,'--input-type=module','-e',script,str(tmp_path)],capture_output=True,timeout=10)
    assert result.returncode==0


def test_interruption_after_running_intent_retains_automatic_attempt_reservation(takeover,monkeypatch):
    import json
    f=takeover;bootstrap(f)
    original=f.ops.prepare_intent
    class Interrupted(BaseException):pass
    def interrupt(s,action,**kw):
        original(s,action,**kw)
        saved=json.loads(f.ops.path.read_text())[s.id]
        assert saved['daemon_restore_attempts']==3
        if action=='start': raise Interrupted()
    monkeypatch.setattr(f.ops,'prepare_intent',interrupt)
    with pytest.raises(Interrupted): f.ops.poll_once()
    restored=ServiceOperations(f.ops.registry,f.pm,f.health,f.ops.runtime,clock=f.clock,monotonic_clock=f.clock)
    assert restored._entry('one')['desired_state']=='running'
    assert restored._entry('one')['daemon_restore_attempts']==3
    with pytest.raises(OperationsError,match='automatic_start_budget_exhausted'):
        restored._reserve_automatic_start(f.s)
    assert f.pm.calls==[('kill','one')]


@pytest.mark.parametrize('changed_on',[1,2,3])
def test_daemon_change_during_termination_preflight_never_reaches_signal(takeover,monkeypatch,changed_on):
    from pm2_manager import Pm2Manager,Pm2Error
    f=takeover
    pm=Pm2Manager(f.ops.runtime,f.ops.runtime/'logs',runner=lambda *_:None)
    class Helper:
        calls=0
        def evaluate_external_identity(self,s):
            return f.pm.evaluate_external_identity(s)
        def kill_external(self,s,**kwargs):
            kwargs['before_signal']()
            self.calls+=1
    helper=Helper();pm._external_helper=helper
    pm.snapshot=lambda **kw:{}
    checks=[]
    def identity(expected):
        checks.append(1)
        return expected if len(checks)<changed_on else 'changed:1'
    monkeypatch.setattr(pm,'_verified_daemon_identity',identity)
    with pytest.raises(Pm2Error,match='daemon changed'):
        pm.kill_external(f.s,expected_pid=99,expected_create_time=1004,expected_daemon_id='102:1005')
    assert helper.calls==0
