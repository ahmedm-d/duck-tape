"""Sourced dependency checks. A version match is not proof of exploitability."""
import json
import hashlib
import re
import tomllib
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import urllib.request
import http.client
import socket
import ssl
import time
import uuid
from pathlib import PurePosixPath
from urllib.parse import urlsplit
from . import db


def dependencies(index):
    records=[]
    gaps=[]
    paths={file['path'] for file in index['files'] if file.get('status')=='indexed'}
    for file in index['files']:
        if file.get('status')!='indexed': continue
        path=file['path']; content=file.get('content','')
        if path.endswith('package-lock.json'):
            try:
                lock=json.loads(content)
                for location,package in lock.get('packages',{}).items():
                    if location and package.get('version') and 'node_modules/' in location:
                        records.append({'ecosystem':'npm','name':package.get('name') or location.rsplit('node_modules/',1)[1],'version':package['version'],'path':path})
                if not lock.get('packages'): gaps.append(f'{path}: only package-lock v2/v3 packages are supported')
            except (ValueError,TypeError): gaps.append(f'{path}: invalid lockfile')
        elif path.endswith(('requirements.txt','requirements.lock')):
            for line in re.sub(r'\\\r?\n', ' ', content).splitlines():
                line=line.strip()
                if not line or line.startswith('#'): continue
                match=re.fullmatch(r'([\w.-]+)==([\w.+!-]+)(?:\s+--hash=sha256:[a-fA-F0-9]{64})*(?:\s*#.*)?',line)
                if match:
                    records.append({'ecosystem':'PyPI','name':match[1],'version':match[2],'path':path})
                else: gaps.append(f'{path}: unresolved requirement {line[:100]}')
        elif path.endswith('uv.lock'):
            try:
                for package in tomllib.loads(content).get('package', []):
                    if package.get('version') and 'registry' in package.get('source', {}):
                        records.append({'ecosystem':'PyPI','name':package['name'],'version':package['version'],'path':path})
                    elif not any(k in package.get('source', {}) for k in ('editable','virtual')):
                        gaps.append(f'{path}: non-registry package {package.get("name", "unknown")} is not covered by OSV version matching')
            except (ValueError, TypeError, KeyError): gaps.append(f'{path}: invalid uv lockfile')
        elif path.endswith(('yarn.lock','pnpm-lock.yaml','poetry.lock','Cargo.lock','go.sum','package.json','pyproject.toml')):
            parent=PurePosixPath(path).parent
            companions=['package-lock.json'] if path.endswith('package.json') else ['uv.lock','requirements.txt','requirements.lock'] if path.endswith('pyproject.toml') else []
            if any(str(parent/name) in paths for name in companions): continue
            gaps.append(f'{path}: exact dependency resolution unsupported; supported inputs are package-lock v2/v3 and pinned requirements')
    unique={(r['ecosystem'],r['name'],r['version']):r for r in records}
    return list(unique.values()),gaps


def verify_security(job,base,candidate,cancel=None):
    """Re-query a sourced advisory against the exact base and candidate lock versions."""
    from . import sandbox
    base,candidate=sandbox.data(base),sandbox.data(candidate)
    started=time.monotonic()
    result={'id':uuid.uuid4().hex,'check_id':'osv-status','phase':'advisory_after','kind':'security',
        'required':True,'status':'unavailable','base_snapshot':base['id'],'patch_hash':candidate['patch_hash'],
        'command':'OSV version queries (before and after)','exit_code':None,'queries':[],
        'advisory_status':'unvalidated','limitations':['A version match does not establish exploitability.']}
    def finish(status,message,advisory_status='unvalidated'):
        result.update(status=status,stdout=message,advisory_status=advisory_status,duration_ms=round((time.monotonic()-started)*1000))
        return result
    def checkpoint():
        if cancel: cancel.raise_if_cancelled()
    checkpoint()
    url=urlsplit(job.get('source_url') or '')
    if url.scheme!='https' or url.hostname!='osv.dev' or not url.path.startswith('/vulnerability/'):
        return finish('unavailable','Security verification needs an original OSV advisory URL and supported exact dependency versions.')
    advisory_id=url.path.removeprefix('/vulnerability/')
    if not re.fullmatch(r'[A-Za-z0-9_-]+',advisory_id):
        return finish('unavailable','The original OSV advisory identifier is invalid.')
    before=sandbox.source_files(base); after=dict(before)
    for edit in candidate.get('files',[]):
        content=edit.get('after',edit.get('content'))
        if content is None: after.pop(edit['path'],None)
        else: after[edit['path']]=content
    def resolved(files):
        return dependencies({'files':[{'path':path,'status':'indexed','content':raw.decode('utf-8',errors='replace') if isinstance(raw,bytes) else raw} for path,raw in files.items()]})
    packages,gaps=resolved(before); updated,after_gaps=resolved(after)
    result['resolution_gaps']={'before':gaps,'after':after_gaps}
    source_key=(job.get('source_key') or '').rsplit(':',2)
    targets=[p for p in packages if len(source_key)!=3 or (p['name'],p['version'])==(source_key[1],source_key[2])]
    if not targets or len(targets)>20:
        return finish('unavailable','Could not identify a bounded set of resolved baseline dependencies for this advisory.')
    identities={advisory_id}; affected=set()
    def lookup(package,phase):
        checkpoint()
        records=query_osv(package)
        checkpoint()
        result['queries'].append({'phase':phase,'package':package,'retrieved_at':db.now(),'source':'https://api.osv.dev/v1/query',
            'advisories':[{'id':r['id'],'aliases':r.get('aliases',[])} for r in records]})
        return records
    try:
        for package in targets:
            for record in lookup(package,'before'):
                aliases={record['id'],*record.get('aliases',[])}
                if identities & aliases:
                    identities.update(aliases); affected.add((package['ecosystem'],package['name']))
        if not affected: return finish('unavailable','The original advisory no longer matches the actual baseline versions; reassess applicability.')
        if after_gaps: return finish('unavailable','Candidate dependency resolution is incomplete: '+'; '.join(after_gaps))
        remaining=[]
        for package in updated:
            if (package['ecosystem'],package['name']) not in affected: continue
            for record in lookup(package,'after'):
                if identities & {record['id'],*record.get('aliases',[])}: remaining.append(package)
    except (OSError,ValueError,KeyError,TypeError,http.client.HTTPException) as exc:
        return finish('unavailable','OSV verification unavailable: '+type(exc).__name__)
    result['advisory_ids']=sorted(identities)
    if remaining: return finish('failed','The sourced advisory still matches candidate resolved versions.','matched_after_change')
    return finish('passed','The advisory matched the baseline and no longer matches candidate resolved versions. This is dependency-version evidence, not proof about exploitability.','not_matched_after_change')


def query_osv(package):
    from .repository import validate_public_url
    payload=json.dumps({'package':{'name':package['name'],'ecosystem':package['ecosystem']},'version':package['version']}).encode()
    parsed,addresses=validate_public_url('https://api.osv.dev/v1/query')
    connection=http.client.HTTPSConnection(parsed.hostname,timeout=10)
    raw_socket=socket.create_connection((addresses[0],443),timeout=10)
    try:
        connection.sock=ssl.create_default_context().wrap_socket(raw_socket,server_hostname=parsed.hostname)
        connection.request('POST','/v1/query',body=payload,headers={'Content-Type':'application/json','User-Agent':'Steward-local/2'})
        response=connection.getresponse()
        if response.status!=200: raise ValueError(f'OSV returned HTTP {response.status}')
        raw=response.read(2_000_001)
    finally:
        connection.close()
        raw_socket.close()
    if len(raw)>2_000_000: raise ValueError('OSV response too large')
    return json.loads(raw).get('vulns',[])


def _lookup(package):
    key=hashlib.sha256(json.dumps([package[k] for k in ('ecosystem','name','version')]).encode()).hexdigest()
    cached=db.get('advisory_cache',key)
    if cached and cached['expires_at']>db.now():
        return {**cached,'package':package,'cached':True}
    try:
        vulnerabilities=query_osv(package)
        return db.put('advisory_cache',{'id':key,'package':package,'retrieved_at':db.now(),
            'expires_at':(datetime.now(timezone.utc)+timedelta(hours=6)).isoformat(),
            'advisories':vulnerabilities,'fixture':False,'source':'https://api.osv.dev/v1/query'})
    except Exception as exc:
        return {'package':package,'error':type(exc).__name__,'retrieved_at':db.now(),'fixture':False}


def scan(repo, snapshot_id=None, cursor=None, cancel=None):
    if isinstance(repo,str):
        repo=db.get('repositories',repo)
        if not repo or snapshot_id not in (None,repo['snapshot'],repo.get('source_snapshot_id')):
            raise ValueError('Scan snapshot is not current in this repository')
    packages,gaps=dependencies(repo['index'])
    signals=[]; records=[]
    public=[]
    for package in packages:
        if package['name']=='fixture-parser':
            if repo.get('is_sample') and package['version']=='1.0.0':
                signals.append({'title':'Fixture advisory: update fixture-parser','description':'Bundled fixture STEWARD-DEMO-001 matches requirements.lock fixture-parser 1.0.0; fixed 1.0.1. This is illustrative fixture data, not a live OSV advisory or evidence of exploitability.','source':'security','type':'security','priority':'high','scenario':'security','source_url':'fixture:STEWARD-DEMO-001','source_key':'STEWARD-DEMO-001:fixture-parser:1.0.0'})
                records.append({'package':package,'advisory':'STEWARD-DEMO-001','retrieved_at':db.now(),'fixture':True})
            continue
        public.append(package)
    failures=0
    with ThreadPoolExecutor(max_workers=4,thread_name_prefix='osv') as pool:
        for record in pool.map(_lookup,public):
            if cancel: cancel.raise_if_cancelled()
            records.append(record)
            package=record['package']
            if record.get('error'):
                failures+=1
                gaps.append(f"OSV lookup unavailable for {package['name']}: {record['error']}; no clean bill of health inferred")
                continue
            groups=[]
            for advisory in record['advisories']:
                identities={advisory['id'],*advisory.get('aliases',[])}
                overlaps=[g for g in groups if g['identities'] & identities]
                merged={'identities':identities,'records':[advisory]}
                for group in overlaps:
                    merged['identities'].update(group['identities']); merged['records'].extend(group['records']); groups.remove(group)
                groups.append(merged)
            for group in groups:
                v=group['records'][0]
                canonical=sorted(group['identities'])[0]
                signals.append({'title':f"{package['name']}: {canonical}",'description':f"OSV version match: {package['name']} {package['version']} in {package['path']}. {v.get('summary','Investigate this advisory')}. Applicability and exploitability require investigation; no exploit claim is made.",'source':'security','type':'security','priority':'high','source_url':'https://osv.dev/vulnerability/'+v['id'],'source_key':f"{canonical}:{package['name']}:{package['version']}"})
    for f in repo['index']['files']:
        if f.get('status')=='indexed' and f.get('path','').endswith('.py') and re.search(r'except\s*:\s*\n\s*pass',f.get('content','')):
            signals.append({'title':f"Investigate swallowed errors in {f['path']}",'description':f"Rule-based hypothesis: {f['path']} contains bare except/pass. Establish expected error handling before calling this a confirmed bug.",'source':'analysis','type':'refactor','priority':'low','source_key':f"bare-except:{f['path']}:{f.get('hash','')}"})
    for edge in repo['index'].get('edges',[]):
        if edge.get('kind')=='import' and edge.get('resolution')=='unresolved' and str(edge.get('target','')).startswith('.'):
            signals.append({'title':'Investigate unresolved local import','description':f"Parser could not resolve {edge.get('target')} from {edge.get('source')}. This may reflect unsupported runtime resolution; establish evidence before calling it a bug.",'source':'analysis','type':'refactor','priority':'low','source_key':'unresolved:'+hashlib.sha256(json.dumps(edge,sort_keys=True).encode()).hexdigest()})
    return {'signals':signals,'records':records,'gaps':gaps,'retrieved_at':db.now(),'scanned':len(records)-failures,
            'resolved':len(packages),'failed':failures,'unresolved':len(gaps)-failures,'complete':not gaps,'next_cursor':None}


def qualify(signal_id, snapshot_id, cancel=None):
    signal=db.get('signals',signal_id)
    if not signal: raise ValueError('Signal not found')
    repo=db.get('repositories',signal['repo_id'])
    if snapshot_id not in {repo['snapshot'],repo.get('source_snapshot_id')}:
        raise ValueError('Signal snapshot is stale')
    if cancel: cancel.raise_if_cancelled()
    from .retrieval import retrieve
    context=retrieve(repo['index'],signal.get('original',''))
    paths=list(dict.fromkeys(item['path'] for item in context['items']))
    return {'affected_areas':paths,'rationale':'Sourced request matched repository evidence: '+(', '.join(paths[:6]) or 'no direct matches; investigation must establish applicability'),
            'qualification':'request' if signal['source'] in {'ticket','request'} else 'hypothesis',
            'approach':'Inspect the retrieved implementations and tests, preserve scoped approved constraints, propose a bounded patch, and verify the acceptance criteria against the unchanged baseline.',
            'context':context,'snapshot':repo['snapshot']}
