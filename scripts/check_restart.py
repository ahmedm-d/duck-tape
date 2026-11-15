"""Kill the actual supervised worker, then restart the API with persisted sample work."""
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

ROOT=Path(__file__).resolve().parents[1]
PORT=8766
BASE=f'http://127.0.0.1:{PORT}/api'


def request(path,method='GET',body=None):
    req=urllib.request.Request(BASE+path,data=json.dumps(body or {}).encode() if method!='GET' else None,
        headers={'Content-Type':'application/json'},method=method)
    with urllib.request.urlopen(req,timeout=10) as response:
        return json.load(response)


def main():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',PORT))
    directory=ROOT/'data'/'restart-check'/uuid.uuid4().hex
    directory.mkdir(parents=True)
    env={**os.environ,'STEWARD_ATLAS_REQUIRED':'0','STEWARD_AUTH_REQUIRED':'0','STEWARD_SUPABASE_URL':'','STEWARD_SUPABASE_PUBLISHABLE_KEY':'','STEWARD_DATA_DIR':str(directory),'STEWARD_MODEL_ENDPOINT':'http://127.0.0.1:1',
        'STEWARD_ATLAS_ENABLED':'0','STEWARD_ATLAS_GRAPH_MIRROR':'0','STEWARD_ATLAS_RETRIEVAL':'0',
        'STEWARD_MODEL':'restart-check-no-model','STEWARD_MODEL_PROVIDER':'openai','STEWARD_SPECIALIST_MODE':'0','PYTHONUNBUFFERED':'1',
        'STEWARD_EMBEDDING_MODEL':'','STEWARD_EMBEDDING_ENDPOINT':'',
        'PYTHONPATH':os.pathsep.join(filter(None,[str(ROOT),str(ROOT/'vendor-current'),os.environ.get('PYTHONPATH')]))}
    process=None
    output=(directory/'backend.log').open('w',encoding='utf-8')
    def stored(kind,id):
        with sqlite3.connect(directory/'steward.sqlite3') as con:
            row=con.execute('SELECT data FROM records WHERE kind=? AND id=?',(kind,id)).fetchone()
        return json.loads(row[0]) if row else None
    def kill(pid):
        if os.name=='nt':
            subprocess.run(['taskkill','/PID',str(pid),'/F'],capture_output=True,check=True)
        else:
            import signal
            os.kill(pid,signal.SIGKILL)
    def wait_for(predicate,timeout=30):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            try:
                value=predicate()
                if value: return value
            except (urllib.error.URLError,TimeoutError,sqlite3.OperationalError):
                pass
            time.sleep(.1)
        raise TimeoutError('Acceptance condition timed out; inspect '+str(directory/'backend.log'))
    def launch():
        child=subprocess.Popen([sys.executable,'run.py','--port',str(PORT)],cwd=ROOT,env=env,
            stdout=output,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            wait_for(lambda: request('/health')['worker']=='running')
        except BaseException:
            shutdown(child)
            raise
        return child
    def shutdown(child):
        service=stored('services','worker')
        if child and child.poll() is None:
            child.kill(); child.wait(timeout=10)
        if service and service.get('instance_id'):
            record={'id':'shutdown:'+service['instance_id'],'requested':True}
            with sqlite3.connect(directory/'steward.sqlite3') as con:
                con.execute('INSERT OR REPLACE INTO records VALUES(?,?,?)',('system',record['id'],json.dumps(record)))
            try:
                wait_for(lambda: (stored('services','worker') or {}).get('status')=='stopped',15)
            except TimeoutError:
                kill(service['pid'])
    started=time.monotonic()
    try:
        process=launch()
        first_api=process.pid
        first_worker=stored('services','worker')['pid']
        repo=request('/repositories')[0]
        knowledge=request('/knowledge?repo_id='+repo['id'])[0]
        approved=request('/knowledge/'+knowledge['id']+'/approve','POST',
            {'version':knowledge['version'],'snapshot':repo['snapshot']})
        job=next(j for j in request('/jobs') if j.get('scenario')=='bug')
        if job['status']=='Failed':
            assert 'knowledge changed' in job.get('error',''),job
            job=request('/jobs/'+job['id']+'/retry','POST')
        queued=request('/jobs/'+job['id']+'/approve','POST')
        assert queued['status']=='Queued',queued['status']
        kill(first_worker)
        persisted=stored('jobs',job['id'])
        assert persisted['status'] in ('Queued','Investigating','Implementing','Verifying'),persisted['status']
        print('Killed worker',first_worker,'with persisted state',persisted['status'],flush=True)
        second_worker=wait_for(lambda: (s if (s:=stored('services','worker')) and s['pid']!=first_worker and s['status']=='running' else None))['pid']
        detail=wait_for(lambda: (d if (d:=request('/jobs/'+job['id']))['status'] in ('Ready for review','Failed') else None),205)
        assert detail['status']=='Ready for review',detail
        assert len(detail['revisions'])==1,len(detail['revisions'])
        revision=detail['revisions'][0]
        checks={c['phase']:c for c in detail['checks']}
        assert checks['baseline']['exit_code']==0
        assert checks['regression-before']['exit_code']!=0
        assert checks['after']['exit_code']==0
        assert any(k['id']==approved['id'] and k['version']==approved['version'] for k in revision['context']['knowledge'])
        assert checks['after']['patch_hash']==revision['patch_hash']
        shutdown(process)
        process=launch()
        persisted_after=request('/jobs/'+job['id'])
        assert persisted_after['current_revision']==revision['id'] and len(persisted_after['revisions'])==1
        evidence={'result':'passed','fixture_provider':True,'method':'Abrupt worker kill, automatic supervisor restart, then full API restart using the same SQLite directory',
            'port':PORT,'first_api_pid':first_api,'second_api_pid':process.pid,'first_worker_pid':first_worker,'replacement_worker_pid':second_worker,
            'persisted_status_at_kill':persisted['status'],'final_status':detail['status'],'revision_count':1,'job_id':job['id'],
            'base_snapshot':job['base_snapshot'],'patch_hash':revision['patch_hash'],'duration_seconds':round(time.monotonic()-started,3),
            'approved_knowledge':approved,'used_knowledge':revision['context']['knowledge'],'checks':detail['checks'],'runs':detail['runs'],'data_directory':str(directory)}
        destination=ROOT/'docs/evidence/restart-check.json'
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_text(json.dumps(evidence,indent=2),encoding='utf-8')
        print('PASS: supervised worker and API recovery; one checked revision and approved knowledge persisted.',destination,flush=True)
    finally:
        if process: shutdown(process)
        output.close()


if __name__=='__main__': main()
