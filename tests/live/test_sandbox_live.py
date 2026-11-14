"""Release gate: fail with diagnostics when the permitted runtime is unavailable."""
import os
import threading
import time
import pytest
from backend import sandbox

pytestmark=pytest.mark.skipif(os.environ.get('STEWARD_LIVE_SANDBOX')!='1',reason='Live sandbox gate requires STEWARD_LIVE_SANDBOX=1; skips are not release evidence')

@pytest.fixture
def live_engine(tmp_path):
    engine=sandbox.DockerSandbox(tmp_path)
    health=engine.health()
    assert health['available'], 'SANDBOX UNAVAILABLE: '+str(health)
    yield engine
    for path in engine.state_dir.glob('*.json'):
        state=__import__('json').loads(path.read_text()); engine.cancel(state['run_id'])

def test_live_active_isolation_probes(live_engine):
    assert all(live_engine.health()['probes'].values())

def test_live_source_copy_and_bounded_logs(live_engine):
    sid=live_engine.prepare('live-copy',{'snapshot':'source','files':{'hello.py':'print("source-copy-ok")'}},{'profile':'python-unittest'},None)
    result=live_engine.run(sid,{'argv':['python','hello.py']},'baseline',None)
    assert result['status']=='passed' and 'source-copy-ok' in result['stdout']
    result=live_engine.run(sid,{'argv':['python','-c','print("x"*1100000)']},'candidate',None)
    assert result['truncated'] and len(result['stdout'])<sandbox.MAX_LOG+100

def test_live_cancel_kills_children(live_engine):
    sid=live_engine.prepare('live-cancel',{'files':{}},{'profile':'python-unittest'},None)
    cancel=threading.Event()
    timer=threading.Timer(.7,cancel.set); timer.start()
    result=live_engine.run(sid,{'argv':['python','-c','import subprocess,time; subprocess.Popen(["sleep","60"]); time.sleep(60)']},'candidate',cancel)
    timer.join()
    assert result['status']=='cancelled'
    with pytest.raises(sandbox.SandboxUnavailable): live_engine.runtime(['container','inspect',sid])

def test_live_process_exhaustion_is_contained(live_engine):
    sid=live_engine.prepare('live-pids',{'files':{}},{'profile':'python-unittest'},None)
    script='import subprocess\nchildren=[]\ntry:\n while True: children.append(subprocess.Popen(["sleep","5"]))\nexcept OSError: print("pids-contained",len(children))\nfinally:\n for child in children: child.kill()'
    result=live_engine.run(sid,{'argv':['python','-c',script],'timeout_seconds':20},'candidate',None)
    assert result['status']=='passed' and 'pids-contained' in result['stdout']

def test_live_scratch_exhaustion_is_contained(live_engine):
    sid=live_engine.prepare('live-disk',{'files':{}},{'profile':'python-unittest'},None)
    script='import os\ntry:\n f=open("fill","wb",buffering=0)\n for i in range(1400): f.write(b"x"*1048576)\n print("unbounded")\nexcept OSError as e: print("scratch-contained",e.errno)'
    result=live_engine.run(sid,{'argv':['python','-c',script],'timeout_seconds':60},'candidate',None)
    assert result['status']=='passed' and 'scratch-contained 28' in result['stdout']

def test_live_python_dependency_and_regression(live_engine,monkeypatch):
    from backend import dependencies, execution_profiles, verification
    monkeypatch.setattr(sandbox,'engine',lambda:live_engine)
    base={'id':'base','files':{'requirements.txt':'six==1.17.0','calc.py':'def add(a,b): return a-b','tests/test_calc.py':'import unittest\nimport six\nfrom calc import add\nclass Test(unittest.TestCase):\n def test_old(self): self.assertTrue(six.PY3)'}}
    candidate={'base_snapshot':'base','patch_hash':'patch','files':[{'path':'calc.py','after':'def add(a,b): return a+b'},{'path':'tests/test_calc.py','after':base['files']['tests/test_calc.py']+'\n def test_add(self): self.assertEqual(add(1,2),3)'}]}
    plan=execution_profiles.discover(base)[0]
    results=verification.run('live-python',base,candidate,plan,None)
    evidence=verification.assess(results,[])
    assert evidence['passed'] and evidence['regression_reproduced'], (evidence,results)

def test_live_node_and_typescript_offline_install(live_engine,monkeypatch):
    import json
    from backend import dependencies, repository
    monkeypatch.setattr(sandbox,'engine',lambda:live_engine)
    metadata=json.loads(repository.fetch_public('https://registry.npmjs.org/typescript/5.8.3',5*1024*1024))
    lock={'name':'live-ts','version':'1.0.0','lockfileVersion':3,'packages':{'':{'name':'live-ts','version':'1.0.0','devDependencies':{'typescript':'5.8.3'}},'node_modules/typescript':{'version':'5.8.3','resolved':metadata['dist']['tarball'],'integrity':metadata['dist']['integrity'],'bin':metadata['bin'],'dev':True}}}
    snapshot={'id':'ts','files':{'package.json':json.dumps({'name':'live-ts','version':'1.0.0','devDependencies':{'typescript':'5.8.3'}}),'package-lock.json':json.dumps(lock),'calc.ts':'export const add=(a:number,b:number):number=>a+b;','test.js':'const test=require("node:test");const assert=require("node:assert");test("sum",()=>assert.equal(1+2,3));'}}
    prepared=dependencies.prepare('live-node',snapshot,{'profile':'node-npm'},None)
    for argv in [['node','--test','test.js'],['./node_modules/.bin/tsc','--noEmit','calc.ts']]:
        result=live_engine.run(prepared['sandbox_id'],{'argv':argv},'candidate',None)
        assert result['status']=='passed',result['stdout']

def test_live_cwd_and_timeout(live_engine):
    sid=live_engine.prepare('live-cwd',{'files':{'nested/hello.py':'print("nested-ok")'}},{'profile':'python-unittest'},None)
    result=live_engine.run(sid,{'argv':['python','hello.py'],'cwd':'nested'},'candidate',None)
    assert result['status']=='passed' and 'nested-ok' in result['stdout']
    result=live_engine.run(sid,{'argv':['python','-c','import time; time.sleep(60)'],'timeout_seconds':.25},'candidate',None)
    assert result['status']=='timed_out'

def test_live_memory_exhaustion_is_contained(live_engine):
    sid=live_engine.prepare('live-memory',{'files':{}},{'profile':'python-unittest'},None)
    result=live_engine.run(sid,{'argv':['python','-c','chunks=[]\nwhile True: chunks.append(bytearray(64*1024*1024))'],'timeout_seconds':30},'candidate',None)
    assert result['status']=='failed' and result['exit_code']==137,result
    assert live_engine.runtime(['info','--format','{{.OSType}}'])=='linux'
