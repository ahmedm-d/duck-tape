"""Opt-in real provider smoke. Does not execute generated repository code."""
import json
import uuid
from pathlib import Path
from backend import db, providers, repository, worker

def main():
    root=Path(__file__).resolve().parents[1]/'data'/'live-smoke'/uuid.uuid4().hex
    root.mkdir(parents=True)
    db.DATA_DIR=root/'state'; db.init()
    repo_root=root/'source'; repo_root.mkdir()
    (repo_root/'greeting.py').write_text('def greet():\n    return "Hello world"\n',encoding='utf-8',newline='')
    (repo_root/'README.md').write_text('Greeting service. greet returns the user greeting.\n',encoding='utf-8',newline='')
    index=repository.index_repository(repo_root)
    repo=db.put('repositories',{'name':'Live transport smoke (second repository)','root':str(repo_root),'is_sample':False,'snapshot':index['snapshot'],'index':index})
    db.put('knowledge',{'repo_id':repo['id'],'title':'Greeting contract','content':'greet must remain a no-argument function returning a string.','scope':'greeting.py','status':'team-approved','version':1})
    job=db.put('jobs',{'repo_id':repo['id'],'title':'Change greeting','description':'In greeting.py replace Hello world with Hello Duck Tape. Keep the function named greet.','risk':'low','status':'Queued','base_snapshot':repo['snapshot'],'source':'request'})
    worker.process_job(job['id'])
    result=db.get('jobs',job['id'])
    evidence={'health':providers.health(),'job':result,'revisions':db.list_records('revisions'),'checks':db.list_records('checks'),'runs':db.list_records('runs')}
    output=Path(__file__).resolve().parents[1]/'data/live-provider-evidence.json'
    output.write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    assert result['status']=='Ready for review',result.get('error')
    revision=evidence['revisions'][0]
    assert any(f['path']=='greeting.py' and 'Hello Duck Tape' in f['after'] for f in revision['files'])
    assert revision['provider']['mode']=='live'
    assert revision['review']['approved']
    assert evidence['checks'][0]['status']=='unavailable'
    assert (repo_root/'greeting.py').read_text()=='def greet():\n    return "Hello world"\n'
    print('Live provider generated and separately reviewed a real patch for a second repository. Execution unavailable (no sandbox). Evidence:',output)

if __name__=='__main__':
    main()
