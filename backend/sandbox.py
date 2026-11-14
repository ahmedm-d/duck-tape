"""Fail-closed Linux Docker execution; repository programs never run on the host."""
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import threading
import time
import uuid

MAX_LOG = 1024 * 1024
LABEL = 'steward.owner'

class SandboxUnavailable(RuntimeError):
    pass

class SandboxCancelled(RuntimeError):
    pass

def data(value):
    return value.model_dump() if hasattr(value, 'model_dump') else value

def cancelled(token):
    if token is None: return False
    if callable(token): return bool(token())
    if hasattr(token, 'is_set'): return token.is_set()
    return bool(getattr(token, 'cancelled', False))

def source_files(snapshot):
    snapshot=data(snapshot)
    files=snapshot.get('files', {})
    if isinstance(files,list) and files and not any(data(f).get('content') is not None for f in files):
        from . import snapshots
        files={name:raw for name,raw in snapshots.read_files(snapshot['id']).items()}
    if isinstance(files,list): files={data(f)['path']:data(f)['content'] for f in files if data(f).get('content') is not None}
    result={}
    for name,content in files.items():
        path=PurePosixPath(name)
        if (not name or path.is_absolute() or '\\' in name or ':' in name or '..' in path.parts or any(part in ('','.','..') for part in name.split('/')) or any(ord(c)<32 for c in name)
            or any(p.casefold() in {'.git','.aws','.ssh'} or p.casefold().startswith('.env') or p.casefold().endswith(('.pem','.key')) or p.rstrip(' .')!=p for p in path.parts)):
            raise ValueError('Unsafe or secret source path: '+name)
        if not isinstance(content,(str,bytes)): raise ValueError('Snapshot content must be text or bytes')
        result[name]=content
    return result

def _cli(args, input=None, timeout=30):
    try:
        result=subprocess.run(['docker',*args],input=input,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise SandboxUnavailable(str(exc)) from exc
    if result.returncode:
        raise SandboxUnavailable(result.stderr.decode(errors='replace')[-4000:] or 'Docker command failed')
    return result.stdout.decode(errors='replace').strip()

class DockerSandbox:
    def __init__(self,state_dir=None,runtime=None):
        from . import db
        self.state_dir=Path(state_dir or os.environ.get('STEWARD_SANDBOX_STATE',Path(db.DATA_DIR)/'sandbox'))
        self.state_dir.mkdir(parents=True,exist_ok=True)
        owner_file=self.state_dir/'owner'
        if not owner_file.exists(): owner_file.write_text(uuid.uuid4().hex,encoding='ascii')
        self.owner=owner_file.read_text(encoding='ascii').strip()
        self.runtime=runtime or _cli
        self._healthy=None
        self._health_time=0

    def image(self,profile):
        if profile not in ('python-unittest','python-pytest','node-npm'): raise SandboxUnavailable('Unsupported execution profile: '+profile)
        name='node' if profile=='node-npm' else 'python'
        configured=os.environ.get('STEWARD_SANDBOX_'+name.upper()+'_IMAGE','steward-'+name+':1')
        identity=self.runtime(['image','inspect',configured,'--format','{{.Id}}'])
        if not identity.startswith('sha256:') or len(identity)!=71: raise SandboxUnavailable('Runtime image identity unavailable')
        return identity

    def _create_args(self,image,run_id,volume=None):
        args=['container','create','--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges:true','--user=10001:10001','--cpus=2','--memory=2g','--memory-swap=2g','--pids-limit=256','--init','--ulimit=nofile=1024:1024','--tmpfs=/tmp:rw,noexec,nosuid,size=256m','--tmpfs=/work:rw,exec,nosuid,size=1g,uid=10001,gid=10001,mode=0700','--workdir=/work','--env=HOME=/tmp','--env=PYTHONDONTWRITEBYTECODE=1','--env=PYTHONUNBUFFERED=1','--label='+LABEL+'='+self.owner,'--label=steward.run='+run_id,'--label=steward.expires='+str(time.time()+1800),'--log-driver=none']
        return args+[image,'sleep','1800']

    def health(self):
        if self._healthy is not None and (self.runtime is not _cli or time.monotonic()-self._health_time<60): return self._healthy
        cid=None
        try:
            if self.runtime(['info','--format','{{.OSType}}'])!='linux': raise SandboxUnavailable('A Linux Docker daemon is required')
            images={}; evidence={}
            for profile,language,extension in [('python-unittest','python','py'),('node-npm','node','js')]:
                identity=self.image(profile); images[language]=identity
                cid=self.runtime(self._create_args(identity,'health'))
                self.runtime(['container','start',cid])
                probe=(Path(__file__).resolve().parents[1]/'sandbox'/'probes'/('isolation.'+extension)).read_text(encoding='utf-8')
                argv=['python','-I','-c',probe] if language=='python' else ['node','-e',probe]
                result=self.runtime(['exec',cid,*argv],timeout=25)
                checks=json.loads(result)
                if not checks or not all(checks.values()): raise SandboxUnavailable('Isolation probe failed: '+result)
                evidence.update({language+'_'+key:value for key,value in checks.items()})
                self.runtime(['container','rm','--force',cid]); cid=None
            self._healthy={'available':True,'runtime':'docker-linux','image':images['python'],'images':images,'probes':evidence}
        except (SandboxUnavailable,ValueError,OSError) as exc:
            self._healthy={'available':False,'runtime':'docker-linux','reason':str(exc)}
        finally:
            if cid:
                try: self.runtime(['container','rm','--force',cid])
                except SandboxUnavailable: pass
        self._health_time=time.monotonic()
        return self._healthy

    def _write(self,state):
        path=self.state_dir/(state['sandbox_id']+'.json')
        temporary=path.with_suffix('.tmp')
        temporary.write_text(json.dumps(state),encoding='utf-8')
        temporary.replace(path)

    def _state(self,sid):
        if not sid or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in sid): raise ValueError('Invalid sandbox ID')
        return json.loads((self.state_dir/(sid+'.json')).read_text(encoding='utf-8'))

    def _owned(self,state):
        try:
            item=json.loads(self.runtime(['container','inspect',state['sandbox_id']]))[0]
        except SandboxUnavailable: return False
        labels=item.get('Config',{}).get('Labels',{}) or {}
        return labels.get(LABEL)==self.owner and labels.get('steward.run')==state['run_id']

    def prepare(self,run_id,snapshot,plan,cancel):
        files=source_files(snapshot)
        snapshot,plan=data(snapshot),data(plan)
        if cancelled(cancel): raise SandboxCancelled('Cancelled before preparation')
        health=self.health()
        if not health['available']: raise SandboxUnavailable(health.get('reason','Active isolation probe unavailable'))
        image=self.image(plan.get('profile','python-unittest'))
        volume=None
        sid=None
        try:
            sid=self.runtime(self._create_args(image,run_id,volume))
            state={'sandbox_id':sid,'run_id':run_id,'owner':self.owner,'volume':volume,'image':image,'base_snapshot':snapshot.get('snapshot',snapshot.get('id')),'created_at':time.time(),'lease_until':time.time()+1800,'status':'prepared'}
            self._write(state)  # Ownership is durable before any imported command can start.
            self.runtime(['container','start',sid])
            modes={item['path']:item.get('mode',0o644)&0o777 for item in snapshot.get('files',[]) if isinstance(item,dict)} if isinstance(snapshot.get('files'),list) else {}
            archive=io.BytesIO()
            with tarfile.open(fileobj=archive,mode='w') as tar:
                for name,content in files.items():
                    raw=content.encode('utf-8') if isinstance(content,str) else content; entry=tarfile.TarInfo(name); entry.size=len(raw); entry.uid=entry.gid=10001; entry.mode=0o755 if modes.get(name,0o644)&0o111 else 0o644
                    tar.addfile(entry,io.BytesIO(raw))
            self.copy_archive(sid,archive.getvalue())
            if cancelled(cancel): raise SandboxCancelled('Cancelled during preparation')
            return sid
        except BaseException:
            if sid:
                try: self.cancel(run_id)
                except SandboxUnavailable: pass

            raise

    def copy_archive(self,sid,archive):
        return self.runtime(['exec','-i','--user','10001:10001',sid,'tar','--extract','--no-same-owner','--file=-','--directory=/work'],input=archive)

    def run(self,sid,command,phase,cancel):
        state=self._state(sid)
        if not self._owned(state): raise SandboxUnavailable('Sandbox ownership mismatch')
        command=data(command)
        argv=command.get('argv')
        if not isinstance(argv,list) or not argv or not all(isinstance(x,str) and '\0' not in x for x in argv): raise ValueError('Command requires explicit argv')
        if cancelled(cancel): self.cancel(state['run_id']); raise SandboxCancelled('Cancelled')
        timeout=min(float(command.get('timeout_seconds',command.get('timeout',300))),600 if phase=='dependencies' else 300)
        if timeout<=0: raise ValueError('Command timeout must be positive')
        started=time.monotonic(); output=bytearray(); truncated=False
        cwd=command.get('cwd','.')
        relative=PurePosixPath(cwd)
        if relative.is_absolute() or '..' in relative.parts or '\\' in cwd or ':' in cwd: raise ValueError('Command cwd must stay inside snapshot')
        args=['exec','--user','10001:10001','--env','PYTHONPATH=/work/.steward-site','--workdir',str(PurePosixPath('/work')/relative),sid,*argv]
        state['status']='running'; state['command']=argv; self._write(state)
        process=subprocess.Popen(['docker',*args],stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        def drain():
            nonlocal truncated
            while True:
                chunk=process.stdout.read(65536)
                if not chunk: break
                remaining=MAX_LOG-len(output)
                output.extend(chunk[:max(remaining,0)])
                if len(chunk)>remaining: truncated=True
        reader=threading.Thread(target=drain,daemon=True); reader.start()
        status=None
        try:
            while process.poll() is None:
                if cancelled(cancel) or time.time()-state['created_at']>1800:
                    status='cancelled'; self.cancel(state['run_id']); break
                if time.monotonic()-started>timeout:
                    status='timed_out'; self.cancel(state['run_id']); break
                time.sleep(.05)
            process.wait(timeout=10)
        finally:
            if process.poll() is None: process.kill(); process.wait()
            reader.join(timeout=10)
        text=output.decode(errors='replace')
        if truncated: text+='\n[output truncated at 1 MiB]'
        return {'id':uuid.uuid4().hex,'run_id':state['run_id'],'phase':phase,'command':argv,'check_id':command.get('id'),'kind':command.get('kind',command.get('purpose','test')),'required':command.get('required',True),'exit_code':process.returncode,'status':status or ('passed' if process.returncode==0 else 'failed'),'duration':time.monotonic()-started,'stdout':text,'truncated':truncated,'environment':state['image'],'base_snapshot':state['base_snapshot'],'patch_hash':command.get('patch_hash')}

    def cancel(self,run_id):
        for path in self.state_dir.glob('*.json'):
            state=json.loads(path.read_text(encoding='utf-8'))
            if state['run_id']!=run_id: continue
            if not self._owned(state): continue
            self.runtime(['container','rm','--force',state['sandbox_id']])
            # Retain verified cleanup for pre-tmpfs resources from earlier installations.
            try:
                if not state.get('volume'):
                    state['status']='cancelled'; self._write(state); continue
                labels=json.loads(self.runtime(['volume','inspect',state['volume'],'--format','{{json .Labels}}']))
                if labels.get(LABEL)==self.owner and labels.get('steward.run')==run_id: self.runtime(['volume','rm',state['volume']])
            except (SandboxUnavailable,ValueError): pass
            state['status']='cancelled'; self._write(state)

    def reconcile(self):
        results=[]
        known={path.stem for path in self.state_dir.glob('*.json')}
        for sid in self.runtime(['container','ls','--all','--quiet','--filter','label='+LABEL+'='+self.owner]).splitlines():
            if any(full.startswith(sid) for full in known): continue
            try:
                item=json.loads(self.runtime(['container','inspect',sid]))[0]
                labels=item.get('Config',{}).get('Labels',{}) or {}
                if labels.get(LABEL)==self.owner and labels.get('steward.run') and float(labels.get('steward.expires','inf'))<=time.time():
                    self.runtime(['container','rm','--force',sid]); results.append({'sandbox_id':sid,'status':'orphan_removed'})
            except (SandboxUnavailable,ValueError): continue
        for path in self.state_dir.glob('*.json'):
            state=json.loads(path.read_text(encoding='utf-8'))
            if state['status']=='cancelled': continue
            if state.get('lease_until',0)>time.time():
                results.append({'sandbox_id':state['sandbox_id'],'status':'active_lease'}); continue
            if not self._owned(state):
                results.append({'sandbox_id':state['sandbox_id'],'status':'ownership_mismatch'}); continue
            self.cancel(state['run_id'])
            results.append({'sandbox_id':state['sandbox_id'],'status':'removed'})
        return results

_default=None

def engine():
    global _default
    from . import db
    configured=Path(os.environ.get('STEWARD_SANDBOX_STATE',Path(db.DATA_DIR)/'sandbox')).resolve()
    if _default is None or _default.state_dir.resolve()!=configured: _default=DockerSandbox(configured)
    return _default

def health(): return engine().health()
def prepare(run_id,snapshot,plan,cancel=None): return engine().prepare(run_id,snapshot,plan,cancel)
def run(sandbox_id,command,phase,cancel=None): return engine().run(sandbox_id,command,phase,cancel)
def cancel(run_id): return engine().cancel(run_id)
def reconcile(): return engine().reconcile()
