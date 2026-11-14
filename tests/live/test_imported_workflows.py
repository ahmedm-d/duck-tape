"""Actual local model + Docker generic journeys, isolated from fixture providers."""
import json
import os
from pathlib import Path
import shutil
import pytest
from backend import db, repository, snapshots, worker, workflow

pytestmark = pytest.mark.skipif(os.getenv('STEWARD_LIVE_PIPELINE') != '1', reason='Requires explicitly configured real local model and Docker')


@pytest.mark.parametrize('language',['python','typescript'])
def test_imported_bug_has_actual_regression_and_candidate_checks(language,tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DATA_DIR',tmp_path/'workspace')
    db.init()
    fixtures=Path(__file__).resolve().parents[1]/'fixtures'/'model_eval'/language
    source=tmp_path/'imported'
    shutil.copytree(fixtures,source,ignore=shutil.ignore_patterns('__pycache__','node_modules'))
    index=repository.index_repository(source)
    snapshot=snapshots.capture(language,source,{})
    repo=db.put('repositories',{'id':language,'name':'Imported '+language,'root':str(source),'index':index,'snapshot':index['snapshot'],'source_snapshot_id':snapshot['id'],'is_sample':False})
    job=db.put('jobs',{'repo_id':repo['id'],'title':'Handle empty average input','description':'In service average([]) must return 0. Preserve average([2,4]) == 3 and greeting behavior. Add a regression test for empty values to existing tests, keep all existing tests, and run it against unchanged implementation before the patch.','type':'fix','status':'Queued','requires_approval':True,'preparation_approved':True,'base_snapshot':repo['snapshot'],'source_snapshot_id':snapshot['id'],'risk':'low'})
    worker.process_job(job['id'])
    planned=db.get('jobs',job['id'])
    if planned['status']=='Awaiting approval':
        assert planned.get('plan_hash') and planned.get('approach')
        workflow.transition(job['id'],'approve',{})
        worker.process_job(job['id'])
    result=db.get('jobs',job['id'])
    artifact={'language':language,'job':result,'runs':db.list_records('runs'),'stages':db.list_records('run_stages'),'checks':db.list_records('checks'),'revisions':db.list_records('revisions'),'attempts':db.list_records('candidate_attempts')}
    output=Path('data/live-pipeline-'+language+'-'+job['id']+'.json')
    output.write_text(json.dumps(artifact,indent=2),encoding='utf-8')
    Path('data/live-pipeline-'+language+'.json').write_text(json.dumps(artifact,indent=2),encoding='utf-8')
    assert result['status']=='Ready for review',str(result)+'; artifact='+str(output)
    checks=db.list_records('checks')
    assert any(record['phase']=='baseline' and record['status']=='passed' for record in checks)
    assert any(record['phase']=='regression_before' and record['status']=='failed' for record in checks)
    assert any(record['phase']=='candidate' and record['status']=='passed' for record in checks)
    assert all(record['environment']['image'].startswith('sha256:') for record in checks if 'environment' in record)
