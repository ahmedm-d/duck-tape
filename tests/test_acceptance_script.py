import json
from pathlib import Path
import subprocess
from scripts import acceptance


def test_deterministic_commands_exclude_opt_in_tests_and_inherited_live_configuration(tmp_path):
    gates=acceptance.commands('deterministic',tmp_path)
    assert {gate['id'] for gate in gates}=={'backend','restart','frontend-build','browser-fixtures'}
    backend=next(gate for gate in gates if gate['id']=='backend')
    assert '--ignore=tests/live' in backend['argv'] and 'not live' in backend['argv']
    for gate in gates:
        assert gate['env']['STEWARD_LIVE_TESTS']=='0'
        assert gate['env']['STEWARD_TEST_URL'] is None
    browser=next(gate for gate in gates if gate['id']=='browser-fixtures')
    assert 'tests/workspace.spec.ts' in browser['argv']
    assert not any('live-ui' in arg or 'completion-review' in arg for arg in browser['argv'])


def test_sandbox_and_live_commands_enable_actual_existing_suites(tmp_path):
    sandbox=acceptance.commands('sandbox',tmp_path)
    assert len(sandbox)==1 and sandbox[0]['env']['STEWARD_LIVE_SANDBOX']=='1'
    assert 'tests/live/test_feature_acceptance_execution.py' in sandbox[0]['argv']
    live=acceptance.commands('live',tmp_path,url='http://127.0.0.1:8767')
    corpus=next(gate for gate in live if gate['id']=='model-qualification')
    assert 'scripts.qualification_driver:driver' in corpus['argv']
    assert '--smoke' not in corpus['argv']
    assert all(gate['required'] for gate in live)
    assert len(acceptance.commands('all',tmp_path))==len(acceptance.commands('deterministic',tmp_path))+len(sandbox)+len(live)


def test_required_blocker_does_not_run_command_and_is_nonzero(tmp_path):
    gate=acceptance.commands('sandbox',tmp_path)[0]
    def forbidden(*args,**kwargs):raise AssertionError('Blocked command ran')
    result=acceptance.execute(gate,{'docker':{'available':False,'reason':'Daemon unavailable'}},tmp_path,runner=forbidden)
    assert result['status']=='blocked' and result['exit_code'] is None
    report=acceptance.summarize('sandbox',[result])
    assert report['status']=='blocked' and acceptance.exit_code(report)!=0


def test_required_failure_takes_precedence_over_blocker_and_cannot_pass():
    results=[{'id':'runtime','required':True,'status':'blocked'},{'id':'checks','required':True,'status':'failed'}]
    report=acceptance.summarize('all',results)
    assert report['status']=='failed' and acceptance.exit_code(report)==1
    assert acceptance.summarize('deterministic',[{'required':True,'status':'passed'}])['status']=='passed'


def test_opted_in_pytest_skips_or_zero_tests_are_blocked(tmp_path):
    gate=acceptance.commands('sandbox',tmp_path)[0]
    for xml in ['<testsuites><testsuite><testcase><skipped /></testcase></testsuite></testsuites>','<testsuites />']:
        def runner(argv,**kwargs):
            Path(gate['junit']).write_text(xml)
            return subprocess.CompletedProcess(argv,0)
        result=acceptance.execute(gate,{'docker':{'available':True}},tmp_path,runner=runner)
        assert result['status']=='blocked'


def test_passing_actual_results_and_missing_qualification_artifact_are_distinct(tmp_path):
    gate=acceptance.commands('sandbox',tmp_path)[0]
    def runner(argv,**kwargs):
        Path(gate['junit']).write_text('<testsuites><testsuite><testcase /></testsuite></testsuites>')
        return subprocess.CompletedProcess(argv,0)
    result=acceptance.execute(gate,{'docker':{'available':True}},tmp_path,runner=runner)
    assert result['status']=='passed' and result['counts']['passed']==1
    corpus=next(gate for gate in acceptance.commands('live',tmp_path) if gate['id']=='model-qualification')
    result=acceptance.execute(corpus,{'docker':{'available':True},'provider':{'available':True},'git':{'available':True}},tmp_path,runner=lambda argv,**kwargs:subprocess.CompletedProcess(argv,0))
    assert result['status']=='blocked'


def test_qualification_false_cannot_be_reported_passed_even_with_zero_exit(tmp_path):
    gate=next(gate for gate in acceptance.commands('live',tmp_path) if gate['id']=='model-qualification')
    def runner(argv,**kwargs):
        Path(gate['qualification']).write_text(json.dumps({'kind':'application_qualification','qualified':False,'results':[]}))
        return subprocess.CompletedProcess(argv,0)
    result=acceptance.execute(gate,{'docker':{'available':True},'provider':{'available':True},'git':{'available':True}},tmp_path,runner=runner)
    assert result['status']=='failed'


def test_backend_url_must_be_explicit_loopback_and_without_credentials():
    assert acceptance.local_url('http://127.0.0.1:8767')
    for value in ['', 'https://example.com', 'http://user:secret@127.0.0.1:8767','http://127.0.0.1:8767/path']:
        assert not acceptance.local_url(value)


def test_main_persists_required_blockers_and_returns_nonzero_without_running_services(monkeypatch,tmp_path):
    import run
    monkeypatch.setattr(run,'load_environment',lambda: None)
    monkeypatch.setattr(acceptance,'probe_capabilities',lambda gates,url:{'docker':{'available':False,'reason':'Unavailable runtime'}})
    assert acceptance.main(['--tier','sandbox','--output',str(tmp_path)])==2
    report=json.loads(next(tmp_path.glob('*/report.json')).read_text())
    assert report['complete'] and report['status']=='blocked'
    assert report['results'][0]['status']=='blocked'


def test_invalid_environment_is_a_redacted_machine_readable_blocker(monkeypatch,tmp_path):
    import run
    def invalid(): raise ValueError('sensitive configuration content')
    monkeypatch.setattr(run,'load_environment',invalid)
    assert acceptance.main(['--tier','deterministic','--output',str(tmp_path)])==2
    report=json.loads(next(tmp_path.glob('*/report.json')).read_text())
    assert report['status']=='blocked' and 'sensitive' not in json.dumps(report)
