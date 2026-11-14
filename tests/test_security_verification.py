from backend import signals


def inputs():
    return ({'source_url':'https://osv.dev/vulnerability/GHSA-test-1234','source_key':'CVE-2026-1234:example:1.0.0'},
        {'id':'base','files':{'requirements.txt':b'example==1.0.0\n'}},
        {'patch_hash':'patch','files':[{'path':'requirements.txt','after':b'example==2.0.0\n'}]})


def test_advisory_status_uses_actual_before_after_versions(monkeypatch):
    calls=[]
    def query(package):
        calls.append(package['version'])
        return [{'id':'GHSA-test-1234','aliases':['CVE-2026-1234']}] if package['version']=='1.0.0' else []
    monkeypatch.setattr(signals,'query_osv',query)
    result=signals.verify_security(*inputs())
    assert result['status']=='passed' and calls==['1.0.0','2.0.0']
    assert result['base_snapshot']=='base' and result['patch_hash']=='patch'
    assert result['advisory_status']=='not_matched_after_change'
    assert result['queries'][0]['retrieved_at']


def test_remaining_advisory_or_lookup_failure_cannot_pass(monkeypatch):
    monkeypatch.setattr(signals,'query_osv',lambda package:[{'id':'GHSA-test-1234'}])
    assert signals.verify_security(*inputs())['status']=='failed'
    monkeypatch.setattr(signals,'query_osv',lambda package:(_ for _ in ()).throw(TimeoutError()))
    result=signals.verify_security(*inputs())
    assert result['status']=='unavailable' and 'TimeoutError' in result['stdout']


def test_supported_companion_lock_is_not_reported_unresolved():
    packages,gaps=signals.dependencies({'files':[
        {'path':'pyproject.toml','status':'indexed','content':'[project]\nname="example"'},
        {'path':'requirements.txt','status':'indexed','content':'six==1.17.0 --hash=sha256:'+'a'*64+'\n'},
    ]})
    assert packages[0]['version']=='1.17.0' and gaps==[]
