import hashlib
import json
import pytest
from backend import dependencies, execution_profiles

def test_python_requires_exact_versions_and_rejects_custom_indexes():
    assert dependencies.artifacts({'files':{'requirements.txt':'example==1.2.3'}})['packages'][0]['version']=='1.2.3'
    for value in ['example>=1.2','--index-url https://evil.test/simple\nexample==1.2','git+https://github.com/a/b','example @ https://evil.test/x.whl']:
        with pytest.raises(dependencies.DependencyDecision): dependencies.artifacts({'files':{'requirements.txt':value}})

def test_npm_same_package_multiple_versions_and_hooks_recorded():
    lock={'lockfileVersion':3,'packages':{'':{},'node_modules/a':{'version':'1.0.0','resolved':'https://registry.npmjs.org/a/-/a-1.0.0.tgz','integrity':'sha512-YQ==','hasInstallScript':True},'node_modules/b/node_modules/a':{'version':'2.0.0','resolved':'https://registry.npmjs.org/a/-/a-2.0.0.tgz','integrity':'sha512-Yg=='}}}
    result=dependencies.artifacts({'files':{'package-lock.json':json.dumps(lock)}})
    assert [p['version'] for p in result['packages']]==['1.0.0','2.0.0']
    assert result['install_policy']=='offline-ignore-scripts' and result['hooks_present']
    lock['packages']['node_modules/a']['resolved']='https://evil.test/a.tgz'
    with pytest.raises(dependencies.DependencyDecision): dependencies.artifacts({'files':{'package-lock.json':json.dumps(lock)}})

def test_uv_exact_wheels_and_hashes():
    lock='version=1\n[[package]]\nname="six"\nversion="1.17.0"\nsource={registry="https://pypi.org/simple"}\nwheels=[{url="https://files.pythonhosted.org/packages/six-1.17.0-py2.py3-none-any.whl",hash="sha256:abc"}]'
    assert dependencies.artifacts({'files':{'uv.lock':lock}})['packages'][0]['name']=='six'

def test_artifact_hash_is_verified(tmp_path):
    with pytest.raises(ValueError,match='hash'):
        dependencies.verify_artifact(b'tampered','sha256:'+hashlib.sha256(b'expected').hexdigest())

def test_profiles_preserve_type_build_and_mark_unknown_commands():
    files={'package.json':json.dumps({'scripts':{'test':'vitest run','typecheck':'tsc --noEmit','build':'vite build','deploy':'curl evil.test'}}),'package-lock.json':'{"lockfileVersion":3,"packages":{}}'}
    plan=execution_profiles.discover({'files':files})[0]
    assert [c['purpose'] for c in plan['commands']]==['test','typecheck','build']
    assert plan['unfamiliar_commands']=={'deploy':'curl evil.test'}

def test_node_typescript_strip_flag_maps_to_exact_argv():
    files={'package.json':json.dumps({'scripts':{'test':'node --experimental-strip-types --test service.test.ts'}}),'package-lock.json':'{"lockfileVersion":3,"packages":{}}'}
    plans=execution_profiles.discover({'files':files})
    assert plans and plans[0]['commands'][0]['argv']==['node','--experimental-strip-types','--test','service.test.ts']


def test_npm_verified_artifacts_use_bounded_offline_cache_batches(tmp_path,monkeypatch):
    import base64
    from types import SimpleNamespace
    from backend import sandbox,repository
    raw=b'reviewed contract artifact'
    integrity='sha512-'+base64.b64encode(hashlib.sha512(raw).digest()).decode()
    packages={'node_modules/p'+str(index):{'version':'1.0.0','resolved':'https://registry.npmjs.org/p'+str(index)+'/-/p'+str(index)+'-1.0.0.tgz','integrity':integrity} for index in range(35)}
    snapshot={'files':{'package-lock.json':json.dumps({'lockfileVersion':3,'packages':{'':{},**packages}})}}
    engine=SimpleNamespace(state_dir=tmp_path,_state=lambda sid:{'image':'sha256:pinned'},copy_archive=lambda sid,archive:None)
    monkeypatch.setattr(sandbox,'engine',lambda:engine)
    monkeypatch.setattr(sandbox,'prepare',lambda *args:'sandbox')
    monkeypatch.setattr(sandbox,'cancel',lambda *args:None)
    monkeypatch.setattr(repository,'fetch_public',lambda *args:raw)
    commands=[]
    def run(sid,command,phase,cancel):
        commands.append(command)
        return {'status':'passed','stdout':''}
    monkeypatch.setattr(sandbox,'run',run)
    prepared=dependencies.prepare('batch',snapshot,{'profile':'node-npm'})
    batches=[command['argv'] for command in commands if command['id']=='npm-cache']
    paths=[[part for part in argv[3:] if not part.startswith('--')] for argv in batches]
    assert list(map(len,paths))==[16,16,3]
    assert [path for batch in paths for path in batch]==['/work/'+record['artifact_path'] for record in prepared['artifacts']]
    assert all(argv[:3]==['npm','cache','add'] and '--offline' in argv and '--ignore-scripts' in argv for argv in batches)
    assert commands[-1]['argv'][:3]==['npm','ci','--offline']
