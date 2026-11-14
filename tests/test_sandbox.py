import json
from pathlib import Path
import pytest
from backend import sandbox

class Runtime:
    def __init__(self): self.calls=[]
    def __call__(self, args, **kwargs):
        self.calls.append((args,kwargs))
        if args[:2]==['image','inspect']: return 'sha256:'+'a'*64
        if args[:2]==['container','inspect']: return json.dumps([{'Config':{'Labels':{'steward.owner':'owner','steward.run':'run'}},'State':{'Running':True}}])
        if args[:2]==['container','create']: return 'container-id'
        if args[:2]==['volume','create']: return 'volume-id'
        return ''

@pytest.fixture
def adapter(tmp_path,monkeypatch):
    runtime=Runtime()
    engine=sandbox.DockerSandbox(tmp_path,runtime=runtime)
    engine.owner='owner'
    engine._healthy={'available':True,'image':'sha256:'+'a'*64}
    return engine,runtime

def test_prepare_has_every_isolation_control_and_persists_before_start(adapter):
    engine,runtime=adapter
    sid=engine.prepare('run',{'snapshot':'base','files':{'test.py':'print(1)'}},{'profile':'python-unittest'},None)
    create=next(args for args,_ in runtime.calls if args[:2]==['container','create'])
    for flag in ['--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges:true','--user=10001:10001','--cpus=2','--memory=2g','--pids-limit=256']:
        assert flag in create
    assert not any('/var/run/docker.sock' in x or 'type=bind' in x or '--privileged' in x for x in create)
    assert (engine.state_dir/(sid+'.json')).exists()
    assert ['container','start',sid] in [a for a,_ in runtime.calls]

def test_prepare_rejects_missing_active_probe(adapter):
    engine,_=adapter
    engine._healthy={'available':False,'reason':'probe unavailable'}
    with pytest.raises(sandbox.SandboxUnavailable,match='probe unavailable'):
        engine.prepare('run',{'files':{}},{'profile':'python-unittest'},None)

@pytest.mark.parametrize('path',['../escape','.env','.aws/key','/root/file','x\\y','x/../../y'])
def test_rejects_unsafe_source_before_copy(adapter,path):
    engine,runtime=adapter
    with pytest.raises(ValueError): engine.prepare('run',{'files':{path:'bad'}},{'profile':'python-unittest'},None)
    assert not any(a[:2]==['container','create'] for a,_ in runtime.calls)

def test_cancel_only_removes_verified_owned_resources(adapter):
    engine,runtime=adapter
    engine.prepare('run',{'files':{}},{'profile':'python-unittest'},None)
    engine.cancel('run')
    assert ['container','rm','--force','container-id'] in [a for a,_ in runtime.calls]

def test_reconcile_ignores_foreign_container(adapter):
    engine,runtime=adapter
    def foreign(args,**kwargs):
        if args[:2]==['container','inspect']: return json.dumps([{'Config':{'Labels':{'steward.owner':'other','steward.run':'run'}}}])
        return runtime(args,**kwargs)
    engine.prepare('run',{'files':{}},{'profile':'python-unittest'},None)
    state=engine._state('container-id'); state['lease_until']=0; engine._write(state)
    engine.runtime=foreign
    assert engine.reconcile()[0]['status']=='ownership_mismatch'
    assert not any(a[:2]==['container','rm'] for a,_ in runtime.calls)

def test_scratch_space_is_bounded_and_secret_aliases_rejected(adapter):
    engine,runtime=adapter
    engine.prepare('run',{'files':{}},{'profile':'python-unittest'},None)
    create=next(a for a,_ in runtime.calls if a[:2]==['container','create'])
    assert '--tmpfs=/work:rw,exec,nosuid,size=1g,uid=10001,gid=10001,mode=0700' in create
    for name in ['.ENV','.AWS/key','.Git/config','x.']:
        with pytest.raises(ValueError): sandbox.source_files({'files':{name:'bad'}})

def test_prepare_failure_removes_owned_container(adapter):
    engine,runtime=adapter
    def failed_copy(args,**kwargs):
        if args and (args[0]=='cp' or args[:2]==['exec','-i']): raise sandbox.SandboxUnavailable('copy failed')
        return runtime(args,**kwargs)
    engine.runtime=failed_copy
    with pytest.raises(sandbox.SandboxUnavailable,match='copy failed'):
        engine.prepare('run',{'files':{'file':'ok'}},{'profile':'python-unittest'},None)
    assert ['container','rm','--force','container-id'] in [a for a,_ in runtime.calls]

def test_reconcile_preserves_unexpired_controller_lease(adapter):
    engine,runtime=adapter
    engine.prepare('run',{'files':{}},{'profile':'python-unittest'},None)
    assert engine.reconcile()[0]['status']=='active_lease'
    assert not any(a[:2]==['container','rm'] for a,_ in runtime.calls)

def test_metadata_snapshot_binary_files_are_preserved(monkeypatch):
    from backend import snapshots
    monkeypatch.setattr(snapshots,'read_files',lambda snapshot_id:{'asset.bin':b'\xff\x00\xfe'})
    assert sandbox.source_files({'id':'base','files':[{'path':'asset.bin','hash':'hash'}]})=={'asset.bin':b'\xff\x00\xfe'}
