"""Run the loopback API and supervise its persistent worker process."""
import argparse
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'vendor-current'))


def load_environment():
    path=ROOT/'.env'
    if not path.is_file(): return
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        if not line.strip() or line.lstrip().startswith('#'): continue
        key,separator,value=line.partition('=')
        key=key.strip()
        if not separator or not (key.startswith('STEWARD_') or key in {'ADAPTION_API_KEY', 'HF_TOKEN'}) or not key.replace('_','').isalnum():
            raise ValueError('Invalid .env entry; use STEWARD_NAME, ADAPTION_API_KEY or HF_TOKEN with literal values')
        value=value.strip()
        if len(value)>=2 and value[0]==value[-1] and value[0] in {'"',"'"}: value=value[1:-1]
        os.environ.setdefault(key,value)

if __name__=='__main__':
    load_environment()
    import uvicorn
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    # Option B: Atlas is a compulsory part of repository knowledge. Importing backend.main never runs this check.
    from backend import atlas_startup
    try: atlas_startup.check_atlas_ready()
    except atlas_startup.AtlasStartupError as exc: raise SystemExit('Duck Tape cannot start: '+str(exc)) from None
    # Supabase accounts are optional once configured; STEWARD_AUTH_REQUIRED=1 makes them compulsory.
    from backend import auth
    try: auth.check_auth_ready()
    except auth.AuthConfigError as exc:
        if auth.auth_required(): raise SystemExit('Duck Tape cannot start: '+str(exc)) from None
        print('Duck Tape: Supabase sign-in unavailable ('+str(exc)+'); the workspace stays open without accounts.',flush=True)
    stop=threading.Event()
    children=[]
    instance_id=uuid.uuid4().hex
    def supervise():
        delay=1
        while not stop.is_set():
            process=subprocess.Popen([sys.executable,'-m','backend.worker_main'],cwd=ROOT,env={**os.environ,'STEWARD_INSTANCE_ID':instance_id},
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            children[:]=[process]
            started=time.monotonic()
            while process.poll() is None and not stop.wait(.5):
                pass
            if stop.is_set():
                from backend import db
                db.put('system',{'id':'shutdown:'+instance_id,'requested':True})
                try: process.wait(timeout=12)
                except subprocess.TimeoutExpired: process.terminate(); process.wait(timeout=5)
                return
            print(f'Duck Tape worker exited ({process.returncode}); restarting in {delay}s',flush=True)
            stop.wait(delay)
            delay=1 if time.monotonic()-started>60 else min(30,delay*2)
    supervisor=threading.Thread(target=supervise,name='worker-supervisor',daemon=True)
    supervisor.start()
    try:
        uvicorn.run('backend.main:app',host='127.0.0.1',port=args.port,log_level='info')
    finally:
        stop.set()
        supervisor.join(timeout=18)
