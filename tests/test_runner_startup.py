"""Real ephemeral TCP/process startup races; no model endpoint or imported code."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest

from backend import providers


def manifest(tmp_path, port):
    executable=tmp_path/'runner'/'llama-server.exe'
    executable.parent.mkdir(exist_ok=True)
    executable.write_bytes(b'reviewed test executable placeholder')
    model=tmp_path/'model.gguf';model.write_bytes(b'reviewed test model placeholder')
    return {'executable':str(executable),'model_file':str(model),'port':port,
            'executable_sha256':hashlib.sha256(executable.read_bytes()).hexdigest(),
            'model_sha256':hashlib.sha256(model.read_bytes()).hexdigest(),
            'endpoint':f'http://127.0.0.1:{port}/v1','alias':'fixture','context_limit':8192,'threads':1}


def available_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]


@pytest.mark.parametrize('embedding',[False,True])
def test_occupied_tcp_port_and_failed_health_never_launches(tmp_path,monkeypatch,embedding):
    with socket.socket() as listener:
        if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
            listener.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        listener.bind(('127.0.0.1',0));listener.listen()
        installed=manifest(tmp_path,listener.getsockname()[1])
        monkeypatch.setattr(providers,'configuration',lambda:{'kind':'openai','endpoint':installed['endpoint'],'model':'fixture',
            'context_limit':8192,'embedding_endpoint':installed['endpoint'],'embedding_model':'fixture'})
        monkeypatch.setattr(providers,'installed_runtime',lambda **kwargs:installed)
        monkeypatch.setattr(providers,'health',lambda:{'available':False})
        monkeypatch.setattr(providers,'request',lambda *args,**kwargs:(_ for _ in ()).throw(TimeoutError()))
        monkeypatch.setattr(providers.subprocess,'Popen',lambda *args,**kwargs:pytest.fail('Occupied port must never launch another runner'))
        result=providers.start_embedding_runner() if embedding else providers.start_local_runner()
        assert not result['started'] and 'port' in result['error'].lower()
        assert ('_embedding_runner_error' if embedding else '_runner_error') in vars(providers)


def test_startup_timeout_terminates_only_the_new_child(tmp_path,monkeypatch):
    installed=manifest(tmp_path,available_port());children=[];real_popen=subprocess.Popen
    def launch(*args,**kwargs):
        child=real_popen([sys.executable,'-c','import time; time.sleep(10)'],**kwargs)
        children.append(child);return child
    monkeypatch.setattr(providers.subprocess,'Popen',launch)
    monkeypatch.setattr(providers,'RUNNER_BIND_TIMEOUT',0.1,raising=False)
    try:
        result=providers._launch_installed(installed)
        assert not result['started'] and 'bind' in result['error'].lower()
        assert len(children)==1 and children[0].poll() is not None
    finally:
        for child in children:
            if child.poll() is None:child.terminate();child.wait(timeout=5)


def test_child_exit_is_an_actionable_failure(tmp_path,monkeypatch):
    installed=manifest(tmp_path,available_port());real_popen=subprocess.Popen
    monkeypatch.setattr(providers.subprocess,'Popen',lambda *args,**kwargs:real_popen([sys.executable,'-c','raise SystemExit(17)'],**kwargs))
    result=providers._launch_installed(installed)
    assert not result['started'] and '17' in result['error']


def test_legacy_runtime_uses_shared_startup_guard(tmp_path,monkeypatch):
    runtime=tmp_path/'data/model-runtime';(runtime/'runner').mkdir(parents=True)
    (runtime/'runner/llama-server.exe').write_bytes(b'fixture')
    monkeypatch.setattr(providers,'__file__',str(tmp_path/'backend/providers.py'))
    monkeypatch.setattr(providers,'configuration',lambda:{'endpoint':'http://127.0.0.1:11435/v1','model':'legacy-fixture'})
    monkeypatch.setattr(providers,'installed_runtime',lambda:None)
    monkeypatch.setattr(providers,'health',lambda:{'available':False})
    calls=[]
    monkeypatch.setattr(providers,'_start_runner',lambda *args,**kwargs:(calls.append((args,kwargs)) or {'started':False,'error':'fixture startup blocked'}),raising=False)
    monkeypatch.setattr(providers.subprocess,'Popen',lambda *args,**kwargs:pytest.fail('Legacy runner bypassed shared guard'))
    result=providers.start_local_runner()
    assert len(calls)==1 and result['error']=='fixture startup blocked'
    assert providers._runner_error==result['error']
    argv=calls[0][0][0]
    assert argv[argv.index('--parallel')+1]=='1'


def test_concurrent_processes_launch_only_one_delayed_listener(tmp_path):
    installed=manifest(tmp_path,available_port());(tmp_path/'manifest.json').write_text(json.dumps(installed))
    helper=tmp_path/'startup_probe.py'
    helper.write_text(r'''import json,os,socket,subprocess,sys,time
from pathlib import Path
from backend import providers
root=Path(sys.argv[1]);tag=sys.argv[2]
providers.__file__=str(root/tag/'backend/providers.py')
installed=json.loads((root/'manifest.json').read_text())
real_popen=subprocess.Popen
child_code="""import os,socket,sys,time
from pathlib import Path
root=Path(sys.argv[1]);port=int(sys.argv[2])
with (root/'spawned.txt').open('a') as stream:stream.write(str(os.getpid())+'\\n')
time.sleep(0.6)
s=socket.socket()
if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):s.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
s.bind(('127.0.0.1',port));s.listen()
deadline=time.monotonic()+15
while not (root/'stop').exists() and time.monotonic()<deadline:time.sleep(0.05)
s.close()
"""
def spawn(*args,**kwargs):
    return real_popen([sys.executable,'-c',child_code,str(root),str(installed['port'])],**kwargs)
providers.subprocess.Popen=spawn
(root/('ready-'+tag)).touch()
while not (root/'go').exists():time.sleep(0.01)
print(json.dumps(providers._launch_installed(installed)),flush=True)
''')
    processes=[]
    env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1])}
    try:
        for name in ('one','two'):
            processes.append(subprocess.Popen([sys.executable,str(helper),str(tmp_path),name],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)))
        until=time.monotonic()+10
        while not all((tmp_path/('ready-'+tag)).exists() for tag in ('one','two')) and time.monotonic()<until:time.sleep(0.02)
        assert all((tmp_path/('ready-'+tag)).exists() for tag in ('one','two'))
        (tmp_path/'go').touch()
        results=[]
        for process in processes:
            out,err=process.communicate(timeout=10)
            assert process.returncode==0,err
            results.append(json.loads(out))
        time.sleep(0.8)
        assert sum(result['started'] for result in results)==1,results
        assert len((tmp_path/'spawned.txt').read_text().splitlines())==1
        with socket.create_connection(('127.0.0.1',installed['port']),timeout=1):pass
        assert any('progress' in result.get('error','') or 'occupied' in result.get('error','') for result in results)
    finally:
        (tmp_path/'stop').touch()
        for process in processes:
            if process.poll() is None:process.terminate();process.wait(timeout=5)

@pytest.mark.parametrize('reasoning',['other',['off'],'off --port 9999',False])
def test_manifest_rejects_unrecognized_reasoning(tmp_path,monkeypatch,reasoning):
    installed=manifest(tmp_path,available_port());installed['reasoning']=reasoning
    runtime=tmp_path/'data/model-runtime';runtime.mkdir(parents=True)
    (runtime/'installed.json').write_text(json.dumps(installed))
    monkeypatch.setattr(providers,'__file__',str(tmp_path/'backend/providers.py'))
    assert providers.installed_runtime() is None


@pytest.mark.parametrize('reasoning',['off','on','auto',None])
def test_manifest_reasoning_flag_reaches_coding_only(tmp_path,monkeypatch,reasoning):
    installed=manifest(tmp_path,available_port())
    if reasoning is not None:installed['reasoning']=reasoning
    runtime=tmp_path/'data/model-runtime';runtime.mkdir(parents=True)
    (runtime/'installed.json').write_text(json.dumps(installed))
    monkeypatch.setattr(providers,'__file__',str(tmp_path/'backend/providers.py'))
    assert providers.installed_runtime()==installed
    calls=[]
    monkeypatch.setattr(providers,'_start_runner',lambda argv,*args:(calls.append(argv) or {'started':True}),raising=False)
    monkeypatch.setattr(providers.subprocess,'Popen',lambda *args,**kwargs:pytest.fail('Launcher guard must own process creation'))
    providers._launch_installed(installed)
    providers._launch_installed(installed,embedding=True)
    assert ('--reasoning' in calls[0]) == (reasoning is not None)
    if reasoning is not None:assert calls[0][calls[0].index('--reasoning')+1]==reasoning
    assert '--reasoning' not in calls[1]
    assert calls[0][calls[0].index('--parallel')+1]=='1'
    assert '--parallel' not in calls[1]

@pytest.mark.parametrize('failure',[OSError,KeyboardInterrupt])
def test_post_spawn_probe_failure_stops_owned_child(tmp_path,monkeypatch,failure):
    installed=manifest(tmp_path,available_port());children=[];real_popen=subprocess.Popen
    def launch(*args,**kwargs):
        child=real_popen([sys.executable,'-c','import time; time.sleep(10)'],**kwargs)
        children.append(child);return child
    def probe(port):
        if children:raise failure('authored probe failure')
        return False
    monkeypatch.setattr(providers.subprocess,'Popen',launch)
    monkeypatch.setattr(providers,'_port_occupied',probe)
    try:
        if failure is KeyboardInterrupt:
            with pytest.raises(KeyboardInterrupt):providers._launch_installed(installed)
        else:
            assert not providers._launch_installed(installed)['started']
        assert len(children)==1 and children[0].poll() is not None
    finally:
        for child in children:
            if child.poll() is None:child.terminate();child.wait(timeout=5)
