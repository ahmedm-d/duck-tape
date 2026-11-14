import json
from backend.signals import dependencies, scan


def test_lock_versions_and_manifest_gaps():
    index={'files':[{'path':'package-lock.json','status':'indexed','content':json.dumps({'packages':{'node_modules/example':{'version':'1.2.3'}}})},{'path':'requirements.txt','status':'indexed','content':'thing>=2\nexact==4.1\n'}]}
    found,gaps=dependencies(index)
    assert {'ecosystem':'npm','name':'example','version':'1.2.3','path':'package-lock.json'} in found
    assert any(d['name']=='exact' and d['version']=='4.1' for d in found)
    assert any('thing' in g for g in gaps)


def test_fixture_scan_is_sourced_and_stable():
    repo={'is_sample':True,'index':{'files':[{'path':'requirements.lock','status':'indexed','content':'fixture-parser==1.0.0'}]}}
    a=scan(repo); b=scan(repo)
    assert a['signals'][0]['source_key']==b['signals'][0]['source_key']
    assert a['signals'][0]['scenario']=='security'
    assert 'fixture' in a['signals'][0]['description'].lower()
