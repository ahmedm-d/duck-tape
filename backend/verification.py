"""Baseline and candidate evidence with explicit regression and advisory limits."""
import hashlib
import json
import math
import re
import statistics
import uuid
from . import dependencies, sandbox

def _record(phase,status,base,patch,**fields):
    return {'id':uuid.uuid4().hex,'phase':phase,'status':status,'base_snapshot':base,'patch_hash':patch,**fields}

def _benchmark(output):
    for line in reversed(output.splitlines()):
        try:
            value=json.loads(line)
            if isinstance(value,dict) and 'samples_seconds' in value: return value
        except ValueError: pass
    return None

def run(run_id,base,candidate,plan,cancel=None):
    base,candidate,plan=sandbox.data(base),sandbox.data(candidate),sandbox.data(plan)
    files=sandbox.source_files(base)
    base_id=base.get('id',base.get('snapshot',''))
    patch=candidate['patch_hash']
    if candidate.get('base_snapshot')!=base_id: raise ValueError('Candidate belongs to another source snapshot')
    changed=dict(files); edits=candidate.get('files',[])
    for item in edits:
        path=item['path']
        after=item.get('after',item.get('content'))
        if after is None: changed.pop(path,None)
        else: changed[path]=after
    sandbox.source_files({'files':changed})
    results=[]
    benchmarks=[sandbox.data(command) for command in plan.get('commands',[]) if sandbox.data(command).get('purpose',sandbox.data(command).get('kind'))=='benchmark']
    harnesses={}; invalid_harness=set()
    raw=lambda content:content.encode('utf-8') if isinstance(content,str) else content
    for command in benchmarks:
        paths=command.get('benchmark_harness',[])
        if not paths:
            argv=command.get('argv',[])
            if argv[:1] in (['python'],['node']) and len(argv) in (2,3): paths=[argv[-1]]
        hashes={path:hashlib.sha256(raw(files[path])).hexdigest() for path in paths if path in files}
        harnesses[command['id']]=hashes
        if not paths or len(hashes)!=len(paths) or any(path not in changed or raw(changed[path])!=raw(files[path]) for path in paths if path in files):
            invalid_harness.add(command['id'])
            results.append(_record('benchmark_integrity','failed',base_id,patch,check_id=command['id'],required=True,kind='benchmark',stdout='Benchmark harness must exist unchanged in baseline and candidate',benchmark_harness=hashes))
    tests=lambda name: bool(re.search(r'(^|/)(tests?/|test_[^/]*|[^/]*[._](?:test|spec)\.)',name))
    weakened=[]
    for name,before in files.items():
        if tests(name) and name not in changed: weakened.append(name+' removed')
        if tests(name) and name in changed:
            before=before.decode('utf-8',errors='replace') if isinstance(before,bytes) else before
            after=changed[name].decode('utf-8',errors='replace') if isinstance(changed[name],bytes) else changed[name]
            skipping=r'(?:\.skip\s*\(|\.todo\s*\(|pytest\.mark\.skip|unittest\.skip|skipTest\s*\()'
            if len(re.findall(skipping,after))>len(re.findall(skipping,before)): weakened.append(name+' adds skipped tests')
            count=lambda text:len(re.findall(r'\bdef test_|\b(?:test|it)\s*\(',text))
            if count(after)<count(before): weakened.append(name+' reduces test declarations')
    if weakened: results.append(_record('test_integrity','failed',base_id,patch,required=True,stdout='; '.join(weakened)))
    test_edits={name:content for name,content in changed.items() if tests(name) and files.get(name)!=content}
    phases=[('baseline',files)]
    if test_edits: phases.append(('regression_before',{**files,**test_edits}))
    phases.append(('candidate',changed))
    environments=[]
    try:
        for phase,tree in phases:
            phase_id=run_id
            prepared=dependencies.prepare(phase_id,{'id':base_id,'files':tree},plan,cancel)
            sid=prepared['sandbox_id']; environments.append(phase_id)
            # Benchmarks run before repository test/build commands can mutate their files.
            for original in sorted(plan.get('commands',[]),key=lambda value:sandbox.data(value).get('purpose',sandbox.data(value).get('kind'))!='benchmark'):
                command={**sandbox.data(original),'patch_hash':patch}
                if command.get('candidate_only') and phase!='candidate': continue
                if command.get('purpose',command.get('kind'))=='benchmark' and (phase=='regression_before' or command['id'] in invalid_harness): continue
                result=sandbox.run(sid,command,phase,cancel)
                result.update(base_snapshot=base_id,patch_hash=patch,environment={'image':prepared['environment'],'dependencies':prepared['cache_key']},kind=command.get('purpose',command.get('kind','test')))
                if result['kind']=='test':
                    output=result.get('stdout','')
                    # Jest/Vitest report suite and test totals separately.
                    summary=re.search(r'^\s*Tests\b(?!\s*(?:Suites|Files))\s*:?\s*(.*)$',output,re.M)
                    counts_output=summary[1] if summary else output
                    count=re.search(r'Ran (\d+) tests?',output) or re.search(r'(?:#|\u2139) tests (\d+)',output)
                    if count: result['test_count']=int(count[1])
                    elif 'no tests ran' in output: result['test_count']=0
                    else:
                        counts=re.findall(r'(\d+) (?:passed|failed|skipped|xfailed|xpassed|errors?)\b',counts_output)
                        if counts: result['test_count']=sum(map(int,counts))
                    skipped=re.findall(r'(\d+) skipped|skipped=(\d+)|(?:#|\u2139) skipped (\d+)',counts_output)
                    result['skipped_count']=sum(int(v) for group in skipped for v in group if v)
                    if result.get('test_count')==0:
                        result['status']='failed'; result['exit_reason']='No tests collected'
                if result['kind']=='benchmark':
                    result['phase']='benchmark_before' if phase=='baseline' else ('benchmark_after' if phase=='candidate' else phase)
                    result['benchmark']=_benchmark(result['stdout'])
                    result['benchmark_harness']=harnesses[command['id']]
                results.append(result)
            sandbox.cancel(phase_id)
    finally:
        for phase_id in environments: sandbox.cancel(phase_id)
    return results

def assess(results,acceptance):
    results=[sandbox.data(r) for r in results]
    baseline=[r for r in results if r.get('phase')=='baseline']
    candidate=[r for r in results if r.get('phase')=='candidate']
    baseline_passed=bool(baseline) and all(r.get('status')=='passed' for r in baseline if r.get('required',True))
    required_ids={r.get('check_id') for r in baseline if r.get('required',True)}
    candidate_ids={r.get('check_id') for r in candidate if r.get('required',True)}
    candidate_passed=bool(candidate) and required_ids.issubset(candidate_ids) and all(r.get('status')=='passed' for r in candidate if r.get('required',True))
    limitations=[]
    regression=[r for r in results if r.get('phase')=='regression_before']
    reproduced=None
    if regression:
        reproduced=any(r.get('status')=='failed' and any(c.get('check_id')==r.get('check_id') and c.get('status')=='passed' for c in candidate) for r in regression)
        if not reproduced: limitations.append('Regression was not reproduced against the unchanged implementation')
    else: limitations.append('No new regression test was run against the unchanged implementation')
    if not baseline_passed: limitations.append('Baseline checks did not all pass')
    benchmarks=[]
    benchmark_ids={r.get('check_id') for r in results if r.get('phase') in ('benchmark_before','benchmark_after')}
    for check_id in sorted(benchmark_ids,key=str):
        before=next((r for r in results if r.get('phase')=='benchmark_before' and r.get('check_id')==check_id),None)
        after=next((r for r in results if r.get('phase')=='benchmark_after' and r.get('check_id')==check_id),None)
        b=before.get('benchmark') if before else None; a=after.get('benchmark') if after else None
        samples=lambda v:bool(isinstance(v,dict) and isinstance(v.get('samples_seconds'),list) and len(v['samples_seconds'])>=3 and all(isinstance(x,(float,int)) and not isinstance(x,bool) and math.isfinite(x) and x>0 for x in v['samples_seconds']))
        valid=bool(before and after and before.get('status')==after.get('status')=='passed' and before.get('environment')==after.get('environment') and before.get('benchmark_harness')==after.get('benchmark_harness') and before.get('base_snapshot')==after.get('base_snapshot') and before.get('patch_hash')==after.get('patch_hash') and samples(a) and samples(b) and a.get('workload') and a['workload']==b.get('workload'))
        item={'check_id':check_id,'valid':valid,'before':b,'after':a}
        if valid: item['improvement_percent']=(statistics.median(b['samples_seconds'])-statistics.median(a['samples_seconds']))/statistics.median(b['samples_seconds'])*100
        else: limitations.append('Benchmark cannot compare unequal environments, workloads, harnesses, evidence bindings or missing raw samples')
        benchmarks.append(item)
    advisories=[]
    for criterion in acceptance:
        criterion=sandbox.data(criterion)
        key=criterion.get('check_id',criterion.get('id'))
        matches=[r for r in candidate if key and r.get('check_id')==key]
        status='validated' if matches and all(r.get('status')=='passed' for r in matches) else ('failed' if matches else 'unvalidated')
        advisories.append({'id':criterion.get('id'),'status':status,'required':criterion.get('required',True)})
    integrity=all(r.get('status')=='passed' for r in results if r.get('phase') in ('test_integrity','benchmark_integrity'))
    if not integrity and any(r.get('phase')=='benchmark_integrity' for r in results): limitations.append('Required benchmark harness integrity failed')
    for before in baseline:
        after=next((r for r in candidate if r.get('check_id')==before.get('check_id')),None)
        if after and (after.get('test_count',0)<before.get('test_count',0) or after.get('skipped_count',0)>before.get('skipped_count',0)):
            integrity=False; limitations.append('Candidate collected fewer tests or skipped more tests than the baseline')
    passed=baseline_passed and candidate_passed and integrity and all(a['status']=='validated' or not a['required'] for a in advisories) and all(b['valid'] for b in benchmarks)
    return {'passed':passed,'baseline_passed':baseline_passed,'candidate_passed':candidate_passed,'regression_reproduced':reproduced,'advisories':advisories,'benchmarks':benchmarks,'limitations':limitations}
