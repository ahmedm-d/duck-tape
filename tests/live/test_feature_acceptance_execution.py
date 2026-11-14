"""Actual sandbox acceptance selection; authored fixed fixtures, no model inference."""
import os
from pathlib import Path
import pytest
from backend import sandbox,verification,execution_profiles,worker

pytestmark=pytest.mark.skipif(os.getenv('STEWARD_LIVE_SANDBOX')!='1',reason='Requires explicitly enabled real Docker boundary')


@pytest.mark.parametrize('language',['python','typescript'])
def test_feature_selection_runs_only_after_candidate_and_observes_target(language,tmp_path,monkeypatch):
    engine=sandbox.DockerSandbox(tmp_path/'sandboxes')
    assert engine.health()['available']
    monkeypatch.setattr(sandbox,'engine',lambda:engine)
    root=Path(__file__).resolve().parents[1]/'fixtures'/'model_eval'/language
    files={path.relative_to(root).as_posix():path.read_bytes() for path in root.rglob('*') if path.is_file() and '__pycache__' not in path.parts}
    source='service.py' if language=='python' else 'service.ts'
    test='tests/test_service.py' if language=='python' else 'service.test.ts'
    name='test_greet' if language=='python' else 'greeting'
    before=files[source].decode()
    after=before.replace('name="world"','name="Steward"') if language=='python' else before.replace('= "world"','= "Steward"')
    assert after!=before
    tests=files[test].decode().replace('Hello world','Hello Steward')
    base={'id':'feature-'+language,'files':files}
    profile=execution_profiles.discover(base)[0]
    coverage=[{'criterion_id':'criterion-1','path':test,'test':name}]
    plan={**profile,'commands':[*profile['commands'],*worker.acceptance_commands(coverage,profile,{test:tests})]}
    candidate={'base_snapshot':base['id'],'patch_hash':'fixed-feature-selection','files':[{'path':source,'after':after},{'path':test,'after':tests}]}
    results=verification.run('feature-selection-'+language,base,candidate,plan)
    selected=[item for item in results if item.get('check_id')=='acceptance-criterion-1']
    assert len(selected)==1 and selected[0]['phase']=='candidate',results
    assert worker.acceptance_observed(selected[0],coverage[0],profile),selected
    assessment=verification.assess(results,[{'id':'criterion-1','check_id':'acceptance-criterion-1','required':True}])
    assert assessment['passed'],assessment
