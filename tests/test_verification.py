import pytest
from backend import verification


@pytest.mark.parametrize('metadata_only', [False, True])
def test_verification_preserves_executable_modes_in_each_phase(tmp_path, monkeypatch, metadata_only):
    import io
    import tarfile
    from backend import sandbox

    archives = []

    def runtime(argv, **kwargs):
        if argv[:2] == ['image', 'inspect']:
            return 'sha256:' + 'a' * 64
        if argv[:2] == ['container', 'create']:
            return 'container-id'
        if argv[:2] == ['exec', '-i']:
            with tarfile.open(fileobj=io.BytesIO(kwargs['input'])) as archive:
                members = {item.name: (item.mode, archive.extractfile(item).read()) for item in archive}
                if members:
                    archives.append(members)
        return ''

    engine = sandbox.DockerSandbox(tmp_path, runtime=runtime)
    engine._healthy = {'available': True}
    monkeypatch.setattr(sandbox, 'engine', lambda: engine)
    monkeypatch.setattr(sandbox, 'cancel', lambda run_id: None)
    base = {'id': 'base', 'files': [
        {'path': 'run.sh', 'content': '#!/bin/sh\nold', 'mode': 0o100755},
        {'path': 'move.sh', 'content': '#!/bin/sh\nmove', 'mode': 0o100755},
        {'path': 'tests/test_old.py', 'content': 'def test_old(): pass', 'mode': 0o100755},
    ]}
    if metadata_only:
        from backend import snapshots
        contents = {item['path']: item.pop('content') for item in base['files']}
        monkeypatch.setattr(snapshots, 'read_files', lambda snapshot_id: contents)
    candidate = {'base_snapshot': 'base', 'patch_hash': 'patch', 'files': [
        {'path': 'run.sh', 'after': '#!/bin/sh\nnew'},
        {'path': 'move.sh', 'after': None, 'mode': None},
        {'path': 'moved.sh', 'after': '#!/bin/sh\nmove', 'mode': 0o755},
        {'path': 'new.py', 'after': 'value = 1'},
        {'path': 'tests/test_old.py', 'after': 'def test_old(): assert True', 'mode': 0o755},
        {'path': 'tests/test_new.py', 'after': 'def test_new(): pass', 'mode': 0o644},
    ]}
    verification.run('run', base, candidate, {'commands': []})

    baseline, regression, changed = archives
    assert baseline['run.sh'] == (0o755, b'#!/bin/sh\nold')
    assert regression['run.sh'] == baseline['run.sh']
    assert regression['move.sh'] == baseline['move.sh']
    assert regression['tests/test_old.py'] == (0o755, b'def test_old(): assert True')
    assert regression['tests/test_new.py'][0] == 0o644
    assert changed['run.sh'] == (0o755, b'#!/bin/sh\nnew')
    assert changed['moved.sh'] == (0o755, b'#!/bin/sh\nmove')
    assert 'move.sh' not in changed
    assert changed['new.py'][0] == 0o644

def result(phase,status='passed',**kwargs):
    return {'phase':phase,'status':status,'check_id':'tests','required':True,'environment':'same','base_snapshot':'base','patch_hash':'patch',**kwargs}

def test_baseline_failure_prevents_verified_claim():
    evidence=verification.assess([result('baseline','failed'),result('candidate')],[])
    assert not evidence['passed'] and not evidence['baseline_passed']

def test_failing_before_passing_after_regression():
    evidence=verification.assess([result('baseline'),result('regression_before','failed'),result('candidate')],[])
    assert evidence['passed'] and evidence['regression_reproduced']

def test_unreproduced_regression_is_explicit():
    evidence=verification.assess([result('baseline'),result('regression_before'),result('candidate')],[])
    assert evidence['regression_reproduced'] is False
    assert 'not reproduced' in ' '.join(evidence['limitations'])

def test_type_build_and_removed_tests_cannot_be_hidden():
    for failed in [result('candidate','failed',kind='typecheck'),result('test_integrity','failed')]:
        assert not verification.assess([result('baseline'),result('candidate'),failed],[])['passed']

def test_benchmark_requires_same_environment_workload_and_raw_samples():
    before=result('benchmark_before',kind='benchmark',benchmark={'workload':'same','samples_seconds':[2,2,2]})
    after=result('benchmark_after',kind='benchmark',benchmark={'workload':'same','samples_seconds':[1,1,1]})
    assert verification.assess([result('baseline'),result('candidate'),before,after],[])['benchmarks'][0]['improvement_percent']==50
    after['environment']='other'
    evidence=verification.assess([result('baseline'),result('candidate'),before,after],[])
    assert not evidence['benchmarks'][0]['valid']

def test_advisory_is_unvalidated_without_post_change_check():
    acceptance=[{'id':'finding-1','type':'advisory','description':'Security recommendation'}]
    evidence=verification.assess([result('baseline'),result('candidate')],acceptance)
    assert evidence['advisories'][0]['status']=='unvalidated' and not evidence['passed']
    evidence=verification.assess([result('baseline'),result('candidate'),result('candidate',check_id='finding-1')],acceptance)
    assert evidence['advisories'][0]['status']=='validated' and evidence['passed']

def test_required_baseline_commands_cannot_disappear():
    checks=[result('baseline'),result('baseline',check_id='build'),result('candidate')]
    assert not verification.assess(checks,[])['passed']

def test_verifier_marks_removed_and_newly_skipped_tests(monkeypatch):
    from backend import dependencies, sandbox
    monkeypatch.setattr(dependencies,'prepare',lambda *args,**kwargs:{'sandbox_id':'sandbox','environment':'image','cache_key':'key'})
    monkeypatch.setattr(sandbox,'run',lambda sid,command,phase,cancel:result(phase,check_id=command['id'],stdout='Ran 1 test\nOK'))
    monkeypatch.setattr(sandbox,'cancel',lambda *args:None)
    base={'id':'base','files':{'tests/test_old.py':'def test_old(): pass'}}
    plan={'commands':[{'id':'tests','argv':['python','-m','unittest'],'purpose':'test'}]}
    for edits in [[{'path':'tests/test_old.py','after':None}],[{'path':'tests/test_old.py','after':'import pytest\n@pytest.mark.skip\ndef test_old(): pass'}]]:
        checks=verification.run('run',base,{'base_snapshot':'base','patch_hash':'patch','files':edits},plan,None)
        assert any(c['phase']=='test_integrity' and c['status']=='failed' for c in checks)

def test_zero_collected_tests_do_not_validate_a_change(monkeypatch):
    from backend import dependencies, sandbox
    monkeypatch.setattr(dependencies,'prepare',lambda *args,**kwargs:{'sandbox_id':'sandbox','environment':'image','cache_key':'key'})
    monkeypatch.setattr(sandbox,'run',lambda sid,command,phase,cancel:result(phase,check_id=command['id'],stdout='Ran 0 tests in 0.000s\nOK'))
    monkeypatch.setattr(sandbox,'cancel',lambda *args:None)
    checks=verification.run('run',{'id':'base','files':{}},{'base_snapshot':'base','patch_hash':'patch','files':[]},{'commands':[{'id':'tests','argv':['python','-m','unittest'],'purpose':'test'}]},None)
    assert not verification.assess(checks,[])['passed']


def test_new_acceptance_command_runs_only_in_candidate(monkeypatch):
    from backend import dependencies,sandbox
    seen=[]
    monkeypatch.setattr(dependencies,'prepare',lambda *args,**kwargs:{'sandbox_id':'sandbox','environment':'image','cache_key':'key'})
    def execute(sid,command,phase,cancel):
        seen.append((phase,command['id']))
        return result(phase,check_id=command['id'],stdout='Ran 1 test\nOK')
    monkeypatch.setattr(sandbox,'run',execute)
    monkeypatch.setattr(sandbox,'cancel',lambda *args:None)
    plan={'commands':[{'id':'tests','argv':['python','-m','unittest'],'purpose':'test'},
                      {'id':'acceptance-one','argv':['python','-m','unittest'],'purpose':'test','candidate_only':True,'required':True}]}
    base={'id':'base','files':{}}
    checks=verification.run('run',base,{'base_snapshot':'base','patch_hash':'patch','files':[{'path':'tests/test_new.py','after':'def test_new(): assert True'}]},plan)
    assert seen==[('baseline','tests'),('regression_before','tests'),('candidate','tests'),('candidate','acceptance-one')]
    assert verification.assess(checks,[{'id':'feature','check_id':'acceptance-one','required':True}])['passed']


def test_candidate_cannot_claim_improvement_by_modifying_or_deleting_harness(monkeypatch):
    from backend import dependencies, sandbox
    monkeypatch.setattr(dependencies,'prepare',lambda *args,**kwargs:{'sandbox_id':'sandbox','environment':'image','cache_key':'key'})
    monkeypatch.setattr(sandbox,'run',lambda sid,command,phase,cancel:result(phase,check_id=command['id'],stdout='{"workload":"fixed","samples_seconds":[1,1,1]}'))
    monkeypatch.setattr(sandbox,'cancel',lambda *args:None)
    base={'id':'base','files':{'benchmark.py':'# original workload'}}
    plan={'commands':[{'id':'tests','argv':['python','-m','unittest'],'purpose':'test'},{'id':'benchmark','argv':['python','benchmark.py'],'purpose':'benchmark','benchmark_harness':['benchmark.py']}]}
    for after in [None,'# removed workload; fake timing']:
        checks=verification.run('run',base,{'base_snapshot':'base','patch_hash':'patch','files':[{'path':'benchmark.py','after':after}]},plan)
        integrity=[check for check in checks if check['phase']=='benchmark_integrity']
        assert integrity and integrity[0]['required'] and integrity[0]['status']=='failed'
        assert not verification.assess(checks,[])['passed']


def test_benchmark_missing_after_or_mismatched_harness_cannot_pass():
    before=result('benchmark_before',check_id='benchmark',benchmark_harness={'benchmark.py':'original'},benchmark={'workload':'fixed','samples_seconds':[2,2,2]})
    ordinary=[result('baseline'),result('candidate')]
    assert not verification.assess([*ordinary,before],[])['passed']
    after={**before,'phase':'benchmark_after','benchmark_harness':{'benchmark.py':'changed'}}
    assert not verification.assess([*ordinary,before,after],[])['benchmarks'][0]['valid']


def test_same_harness_is_bound_and_regression_phase_does_not_repeat_benchmark(monkeypatch):
    from backend import dependencies, sandbox
    seen=[]
    monkeypatch.setattr(dependencies,'prepare',lambda *args,**kwargs:{'sandbox_id':'sandbox','environment':'image','cache_key':'key'})
    def run(sid,command,phase,cancel):
        seen.append((phase,command['id']))
        return result(phase,check_id=command['id'],stdout='{"workload":"fixed","samples_seconds":[1,1,1]}' if command['id']=='benchmark' else 'Ran 1 test\nOK')
    monkeypatch.setattr(sandbox,'run',run)
    monkeypatch.setattr(sandbox,'cancel',lambda *args:None)
    base={'id':'base','files':{'benchmark.py':'# original workload','tests/test_old.py':'def test_old(): pass'}}
    plan={'commands':[{'id':'tests','argv':['python','-m','unittest'],'purpose':'test'},{'id':'benchmark','argv':['python','benchmark.py'],'purpose':'benchmark','benchmark_harness':['benchmark.py']}]}
    checks=verification.run('run',base,{'base_snapshot':'base','patch_hash':'patch','files':[{'path':'tests/test_old.py','after':'def test_old(): assert True'}]},plan)
    benchmarks=[check for check in checks if check['kind']=='benchmark']
    assert len(benchmarks)==2 and benchmarks[0]['benchmark_harness']==benchmarks[1]['benchmark_harness']
    assert benchmarks[0]['benchmark_harness']['benchmark.py']
    assert ('regression_before','benchmark') not in seen


def test_framework_counts_exclude_suite_summaries_and_preserve_other_reporters(monkeypatch):
    from backend import dependencies,sandbox
    monkeypatch.setattr(dependencies,'prepare',lambda *args,**kwargs:{'sandbox_id':'sandbox','environment':'image','cache_key':'key'})
    monkeypatch.setattr(sandbox,'cancel',lambda *args:None)
    outputs=[
        ('Test Suites: 1 passed, 1 total\nTests: 2 skipped, 1 passed, 3 total\n',3,2),
        (' Test Files  1 passed (1)\n      Tests  1 passed | 2 skipped (3)\n',3,2),
        ('================ 1 passed, 2 skipped in 0.01s ================',3,2),
        ('Ran 3 tests in 0.01s\nOK (skipped=2)',3,2),
        ('# tests 3\n# pass 1\n# skipped 2',3,2),
    ]
    for stdout,count,skipped in outputs:
        monkeypatch.setattr(sandbox,'run',lambda sid,command,phase,cancel:result(phase,stdout=stdout))
        checks=verification.run('counts',{'id':'base','files':{}},{'base_snapshot':'base','patch_hash':'patch','files':[]},{'commands':[{'id':'tests','argv':['runner'],'purpose':'test'}]})
        assert all(check['test_count']==count and check['skipped_count']==skipped for check in checks)


def test_recovered_baseline_requires_unchanged_suite_evidence():
    checks = [result('baseline', 'failed', kind='test', test_count=5),
              result('regression_before', 'failed', kind='test'),
              result('candidate', kind='test', test_count=6),
              result('baseline_recheck', kind='test', test_count=5)]
    evidence = verification.assess(checks, [])
    assert evidence['passed'] and evidence['baseline_recovered']
    assert not evidence['baseline_passed']
    assert 'Baseline checks did not all pass' in evidence['limitations']
    for field, value in [('status', 'failed'), ('environment', 'different'),
                         ('patch_hash', 'different'), ('base_snapshot', 'different'),
                         ('test_count', 4), ('skipped_count', 1)]:
        altered = [*checks[:-1], {**checks[-1], field: value}]
        assert not verification.assess(altered, [])['passed']
    assert not verification.assess(checks[:-1], [])['passed']
    assert not verification.assess([checks[0], *checks[2:]], [])['passed']
    assert not verification.assess([{**checks[0], 'kind': 'build'}, *checks[1:]], [])['passed']
    assert not verification.assess([*checks, result('baseline', 'failed', check_id='other', kind='test')], [])['passed']


def test_baseline_recheck_uses_original_tests_and_candidate_implementation(monkeypatch):
    from backend import dependencies, sandbox
    trees = []
    def prepare(run_id, source, plan, cancel):
        trees.append({f['path']: f['content'] for f in source['files']})
        return {'sandbox_id': 'sandbox', 'environment': 'image', 'cache_key': 'key'}
    monkeypatch.setattr(dependencies, 'prepare', prepare)
    monkeypatch.setattr(sandbox, 'cancel', lambda *args: None)
    monkeypatch.setattr(sandbox, 'run', lambda sid, command, phase, cancel:
                        result(phase, 'failed' if phase in ('baseline', 'regression_before') else 'passed',
                               stdout='Ran 1 test in 0.001s\n'))
    base = {'id': 'base', 'files': {'app.py': 'broken', 'tests/test_old.py': 'original assertions', 'conftest.py': 'original hooks', 'pytest.ini': 'original config', 'vitest.config.ts': 'original JS config'}}
    candidate = {'base_snapshot': 'base', 'patch_hash': 'patch', 'files': [
        {'path': 'app.py', 'after': 'fixed'},
        {'path': 'conftest.py', 'after': 'disable assertions'},
        {'path': 'pytest.ini', 'after': 'exclude tests'},
        {'path': 'vitest.config.ts', 'after': 'disable JS checks'},
        {'path': 'tests/conftest.py', 'after': 'new disabling hook'},
        {'path': 'tests/test_old.py', 'after': 'changed assertions'},
        {'path': 'tests/test_new.py', 'after': 'new assertions'}]}
    checks = verification.run('run', base, candidate,
                              {'commands': [{'id': 'tests', 'purpose': 'test', 'argv': ['python', '-m', 'unittest']}]})
    assert trees[-1] == {'app.py': 'fixed', 'tests/test_old.py': 'original assertions', 'conftest.py': 'original hooks', 'pytest.ini': 'original config', 'vitest.config.ts': 'original JS config'}
    assert checks[-1]['phase'] == 'baseline_recheck'
    assert verification.assess(checks, [])['passed']
