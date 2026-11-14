"""Exact public registry artifacts are fetched by the controller, installed offline."""
import base64
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile
import time
import tomllib
from urllib.parse import urlparse, quote
from . import repository, sandbox

class DependencyDecision(ValueError):
    pass

def verify_artifact(raw,integrity):
    if ':' in integrity and integrity.startswith(('sha256:','sha512:')):
        algorithm,expected=integrity.split(':',1); actual=hashlib.new(algorithm,raw).hexdigest()
    else:
        try: algorithm,expected=integrity.split('-',1)
        except ValueError: raise ValueError('Missing artifact hash')
        if algorithm not in ('sha256','sha384','sha512'): raise ValueError('Unsupported artifact hash')
        actual=base64.b64encode(hashlib.new(algorithm,raw).digest()).decode()
    if actual!=expected: raise ValueError('Artifact hash mismatch')
    return hashlib.sha256(raw).hexdigest()

def _registry_url(url,host):
    parsed=urlparse(url)
    if parsed.scheme!='https' or parsed.hostname!=host or parsed.username or parsed.password or parsed.port not in (None,443) or parsed.fragment or parsed.query:
        raise DependencyDecision('Only exact public '+host+' registry artifacts are supported')
    return url

def artifacts(snapshot):
    files={name:content.decode('utf-8',errors='replace') if isinstance(content,bytes) else content for name,content in sandbox.source_files(snapshot).items()}
    packages=[]; hooks=False
    if 'package-lock.json' in files:
        lock=json.loads(files['package-lock.json'])
        if lock.get('lockfileVersion') not in (2,3): raise DependencyDecision('npm requires package-lock v2 or v3')
        for path,item in lock.get('packages',{}).items():
            if not path: continue
            if item.get('link') or not item.get('version') or not item.get('integrity'): raise DependencyDecision('npm links and unlocked dependencies need a product decision')
            url=_registry_url(item.get('resolved',''),'registry.npmjs.org')
            if not item['integrity'].startswith(('sha256-','sha384-','sha512-')): raise DependencyDecision('npm artifacts require strong integrity hashes')
            packages.append({'name':path.rsplit('node_modules/',1)[-1],'path':path,'version':item['version'],'url':url,'integrity':item['integrity']})
            hooks=hooks or bool(item.get('hasInstallScript'))
        manager='npm'; locktext=files['package-lock.json']
    elif 'uv.lock' in files:
        locktext=files['uv.lock']; lock=tomllib.loads(locktext)
        for item in lock.get('package',[]):
            source=item.get('source',{})
            if source.get('virtual')=='.' or source.get('editable')=='.': continue
            if source.get('registry')!='https://pypi.org/simple': raise DependencyDecision('uv Git, URL, custom registry and editable dependencies need a product decision')
            wheels=[]
            for wheel in item.get('wheels',[]):
                url=_registry_url(wheel['url'],'files.pythonhosted.org')
                wheels.append({'url':url,'integrity':wheel['hash']})
            if not wheels: raise DependencyDecision('Only locked Python wheels are supported; source builds require a product decision')
            packages.append({'name':item['name'],'version':item['version'],'wheels':wheels})
        manager='python'
    elif 'requirements.txt' in files:
        locktext=files['requirements.txt']
        joined=locktext.replace('\\\n',' ')
        for line in joined.splitlines():
            line=line.split(' #',1)[0].strip()
            if not line or line.startswith('#'): continue
            match=re.fullmatch(r'([A-Za-z0-9][A-Za-z0-9_.-]*)==([A-Za-z0-9][A-Za-z0-9_.+!-]*)(?:\s+--hash=sha256:[a-f0-9]{64})*',line)
            if not match: raise DependencyDecision('Python requires exact pinned requirements without URLs, indexes, markers or includes')
            packages.append({'name':match[1],'version':match[2],'hashes':re.findall(r'--hash=(sha256:[a-f0-9]{64})',line)})
        manager='python'
    else:
        if 'package.json' in files: raise DependencyDecision('npm requires a package-lock')
        # No dependency declaration means the fixed standard-library runtime is complete.
        if any(name in files for name in ('pyproject.toml','setup.py','Pipfile')): raise DependencyDecision('Python dependencies require requirements.txt or uv.lock')
        manager='python'; locktext=''
    if len(packages)>2048: raise DependencyDecision('Dependency count exceeds the supported preparation limit')
    for package in packages:
        if manager=='python' and (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*',package['name']) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+!-]*',package['version'])):
            raise DependencyDecision('Invalid exact Python package identity')
    return {'manager':manager,'packages':packages,'hooks_present':hooks,'install_policy':'offline-ignore-scripts','lock_hash':hashlib.sha256(locktext.encode()).hexdigest()}

def _wheel_supported(url):
    filename=url.rsplit('/',1)[-1]
    if not filename.endswith('.whl'): return False
    if filename.endswith(('-py3-none-any.whl','-py2.py3-none-any.whl')): return True
    return bool(re.search(r'-(?:cp312-cp312|cp3[2-9]-abi3)-manylinux[^/]*x86_64\.whl$',filename))

def _resolve(package):
    if 'wheels' in package:
        choices=[p for p in package['wheels'] if _wheel_supported(p['url'])]
    else:
        metadata=json.loads(repository.fetch_public('https://pypi.org/pypi/'+quote(package['name'],safe='')+'/'+quote(package['version'],safe='')+'/json',5*1024*1024))
        choices=[{'url':_registry_url(p['url'],'files.pythonhosted.org'),'integrity':'sha256:'+p['digests']['sha256']} for p in metadata['urls'] if not p.get('yanked') and _wheel_supported(p['url'])]
    hashes=package.get('hashes')
    if hashes: choices=[p for p in choices if p['integrity'] in hashes]
    if not choices: raise DependencyDecision('No locked wheel for Linux x86_64 CPython3.12: '+package['name'])
    return sorted(choices,key=lambda p:p['url'])[0]

def prepare(run_id,snapshot,plan,cancel=None):
    manifest=artifacts(snapshot); plan=sandbox.data(plan)
    if manifest['hooks_present'] and plan.get('install_policy','offline-ignore-scripts')!='offline-ignore-scripts':
        raise DependencyDecision('Package lifecycle scripts require an explicit supported policy')
    sid=sandbox.prepare(run_id,snapshot,plan,cancel)
    engine=sandbox.engine(); state=engine._state(sid)
    identity=state['image']
    key=hashlib.sha256(json.dumps({'lock':manifest['lock_hash'],'image':identity,'platform':'linux/amd64','policy':manifest['install_policy']},sort_keys=True).encode()).hexdigest()
    cache=engine.state_dir/'artifacts'; cache.mkdir(exist_ok=True)
    records=[]; archive=io.BytesIO(); started=time.monotonic(); acquired=0
    cached=sum(p.stat().st_size for p in cache.iterdir() if p.is_file())
    try:
        with tarfile.open(fileobj=archive,mode='w') as tar:
            for index,package in enumerate(manifest['packages']):
                if sandbox.cancelled(cancel): raise sandbox.SandboxCancelled('Dependency preparation cancelled')
                if time.monotonic()-started>600: raise sandbox.SandboxUnavailable('Dependency preparation exceeded ten minutes')
                artifact=package if manifest['manager']=='npm' else _resolve(package)
                url=artifact['url']; filename=url.rsplit('/',1)[-1]
                if not re.fullmatch(r'[A-Za-z0-9_.+%~-]+',filename): raise DependencyDecision('Unsafe artifact filename')
                target=cache/hashlib.sha256((url+artifact['integrity']).encode()).hexdigest()
                raw=target.read_bytes() if target.exists() else repository.fetch_public(url,40*1024*1024)
                digest=verify_artifact(raw,artifact['integrity'])
                acquired+=len(raw)
                if acquired>512*1024*1024: raise DependencyDecision('Artifacts exceed the 512MiB preparation limit')
                if not target.exists():
                    if cached+len(raw)>512*1024*1024: raise DependencyDecision('Verified artifact cache is full; remove obsolete cache artifacts before preparing more dependencies')
                    target.write_bytes(raw); cached+=len(raw)
                member='.steward-deps/'+str(index)+'/'+filename if manifest['manager']=='npm' else '.steward-deps/'+filename
                entry=tarfile.TarInfo(member); entry.size=len(raw); entry.uid=entry.gid=10001; entry.mode=0o644
                tar.addfile(entry,io.BytesIO(raw))
                records.append({**package,'url':url,'integrity':artifact['integrity'],'sha256':digest,'artifact_path':member})
        engine.copy_archive(sid,archive.getvalue())
        results=[]
        if manifest['manager']=='python' and records:
            argv=['python','-m','pip','install','--no-index','--find-links=/work/.steward-deps','--target=/work/.steward-site','--no-compile',*[p['name']+'=='+p['version'] for p in manifest['packages']]]
            results.append(sandbox.run(sid,{'id':'dependency-install','argv':argv,'timeout_seconds':600,'kind':'dependencies'},'dependencies',cancel))
        elif manifest['manager']=='npm':
            for offset in range(0,len(records),16):
                paths=['/work/'+record['artifact_path'] for record in records[offset:offset+16]]
                results.append(sandbox.run(sid,{'id':'npm-cache','argv':['npm','cache','add',*paths,'--cache=/work/.steward-cache','--ignore-scripts','--offline'],'timeout_seconds':600},'dependencies',cancel))
            results.append(sandbox.run(sid,{'id':'dependency-install','argv':['npm','ci','--offline','--ignore-scripts','--cache=/work/.steward-cache','--no-audit','--no-fund'],'timeout_seconds':600},'dependencies',cancel))
        if any(r['status']!='passed' for r in results): raise sandbox.SandboxUnavailable('Offline dependency installation failed: '+next(r['stdout'] for r in results if r['status']!='passed'))
        key=hashlib.sha256((key+json.dumps([(r['url'],r['sha256']) for r in records],sort_keys=True)).encode()).hexdigest()
        manifest.update(sandbox_id=sid,environment=identity,cache_key=key,artifacts=records,checks=results)
        manifests=engine.state_dir/'manifests'; manifests.mkdir(exist_ok=True)
        manifest_path=manifests/(sid+'-dependencies.json')
        manifest_path.write_text(json.dumps(manifest,sort_keys=True),encoding='utf-8')
        return manifest
    except BaseException:
        sandbox.cancel(run_id)
        raise
