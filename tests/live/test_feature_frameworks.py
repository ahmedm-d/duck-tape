"""Authored framework fixtures; metadata resolution is trusted, execution stays offline in Docker."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid
import pytest
from backend import db,dependencies,execution_profiles,sandbox,verification,worker

pytestmark=[pytest.mark.live,pytest.mark.skipif(os.getenv('STEWARD_LIVE_SANDBOX')!='1',reason='Actual framework acceptance requires STEWARD_LIVE_SANDBOX=1')]


def npm_lock(root,framework,version):
    """Resolve only an exact, reviewed manifest; do not install or execute packages."""
    root.mkdir()
    package={'name':'duck-tape-reviewed-'+framework,'version':'1.0.0','private':True,'devDependencies':{framework:version},'scripts':{'test':framework+' run' if framework=='vitest' else 'jest --runInBand'}}
    if framework=='vitest': package['type']='module'
    text=json.dumps(package)
    (root/'package.json').write_text(text,encoding='utf-8')
    for name in ('empty-user.npmrc','empty-global.npmrc'): (root/name).write_text('',encoding='utf-8')
    environment={key:value for key,value in os.environ.items() if not key.casefold().startswith('npm_config_') and key not in {'NPM_TOKEN','NODE_AUTH_TOKEN','NODE_OPTIONS','NODE_PATH'}}
    environment.update(NPM_CONFIG_USERCONFIG=str(root/'empty-user.npmrc'),NPM_CONFIG_GLOBALCONFIG=str(root/'empty-global.npmrc'),NPM_CONFIG_CACHE=str(root/'metadata-cache'),NPM_CONFIG_REGISTRY='https://registry.npmjs.org')
    npm=shutil.which('npm.cmd' if os.name=='nt' else 'npm')
    assert npm,'BLOCKED: trusted npm metadata resolver unavailable'
    command=[npm,'install','--package-lock-only','--ignore-scripts','--no-audit','--no-fund','--registry=https://registry.npmjs.org']
    result=subprocess.run(command,cwd=root,env=environment,capture_output=True,text=True,timeout=180)
    assert result.returncode==0,'BLOCKED: trusted metadata-only lock resolution failed: '+result.stderr[-1500:]
    assert not (root/'node_modules').exists(),'Metadata resolution unexpectedly installed package code on the host'
    lock=(root/'package-lock.json').read_text(encoding='utf-8')
    assert json.loads(lock)['packages']['node_modules/'+framework]['version']==version
    return {'package.json':text,'package-lock.json':lock},command


def fixture(framework):
    if framework=='pytest':
        source='def greet():\n return "Hello world"\n'
        tests='import pytest\nfrom service import greet\ndef test_requested_behavior():\n assert greet()=="Hello world"\ndef test_requested_behavior_extra():\n assert greet()=="Hello world"\n@pytest.mark.skip(reason="Unrelated deliberate fixture skip")\ndef test_unrelated_skip():\n assert False\n'
        pins='pytest==8.3.5\niniconfig==2.1.0\npackaging==25.0\npluggy==1.5.0\n'
        return {'requirements.txt':pins,'service.py':source,'tests/test_service.py':tests},'service.py','tests/test_service.py','test_requested_behavior'
    if framework=='jest':
        source='exports.greet=()=>"Hello world";\n'
        header='const {greet}=require("./service");\n'
        extra={'jest.config.cjs':'module.exports={maxWorkers:1,testEnvironment:"node",testMatch:["**/service.test.js"]};\n'}
    else:
        source='export const greet=()=>"Hello world";\n'
        header='import {test,expect} from "vitest";\nimport {greet} from "./service.js";\n'
        extra={'vitest.config.mjs':'import {defineConfig} from "vitest/config";\nexport default defineConfig({test:{pool:"forks",minWorkers:1,maxWorkers:1,fileParallelism:false,include:["service.test.js"]}});\n'}
    tests=header+'test("requested behavior",()=>expect(greet()).toBe("Hello world"));\ntest("requested behavior extra",()=>expect(greet()).toBe("Hello world"));\ntest.skip("unrelated skip",()=>expect(false).toBe(true));\n'
    return {**extra,'service.js':source,'service.test.js':tests},'service.js','service.test.js','requested behavior'


@pytest.mark.parametrize(('framework','version'),[('pytest','8.3.5'),('jest','29.7.0'),('vitest','2.1.9')])
def test_actual_framework_exact_acceptance_target(framework,version,tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DATA_DIR',tmp_path/'state'); db.init()
    engine=sandbox.DockerSandbox(tmp_path/'sandbox')
    assert engine.health()['available'],'BLOCKED: actual sandbox unavailable: '+str(engine.health())
    monkeypatch.setattr(sandbox,'engine',lambda:engine)
    files,source,test,name=fixture(framework)
    metadata_command=None
    if framework!='pytest':
        manifests,metadata_command=npm_lock(tmp_path/'reviewed-metadata',framework,version)
        files.update(manifests)
    base={'id':'framework-'+framework,'files':files}
    profile=execution_profiles.discover(base)[0]
    after_source=files[source].replace('Hello world','Hello Duck Tape')
    after_tests=files[test].replace('Hello world','Hello Duck Tape')
    reference={'criterion_id':'requested','path':test,'test':name}
    command=worker.acceptance_commands([reference],profile,{test:after_tests})[0]
    plan={**profile,'commands':[*profile['commands'],command]}
    candidate={'base_snapshot':base['id'],'patch_hash':'reviewed-'+framework,'files':[{'path':source,'after':after_source},{'path':test,'after':after_tests}]}
    preparations=[]; original=dependencies.prepare
    def prepare(*args,**kwargs):
        prepared=original(*args,**kwargs)
        version_result=sandbox.run(prepared['sandbox_id'],{'id':'runtime-version','argv':['python','--version'] if framework=='pytest' else ['node','--version']},'runtime_identity',None)
        preparations.append({'environment':prepared['environment'],'cache_key':prepared['cache_key'],'runtime':version_result['stdout'],'artifacts':prepared['artifacts']})
        return prepared
    monkeypatch.setattr(dependencies,'prepare',prepare)
    results=[]; negative=None
    try:
        results=verification.run('framework-'+framework,base,candidate,plan)
        selected=[record for record in results if record.get('check_id')==command['id']]
        skipped=after_tests.replace('def test_requested_behavior():','@pytest.mark.skip(reason="Mapped target deliberately skipped")\ndef test_requested_behavior():') if framework=='pytest' else after_tests.replace('test("requested behavior",','test.skip("requested behavior",')
        # Use verification's reporter parsing as well as the real runner selector.
        skipped_candidate={'base_snapshot':base['id'],'patch_hash':'skipped-'+framework,'files':[{'path':source,'after':after_source},{'path':test,'after':skipped}]}
        negative_results=verification.run('framework-skipped-'+framework,base,skipped_candidate,plan)
        negative=next(record for record in negative_results if record.get('check_id')==command['id'])
        positive=selected[0] if len(selected)==1 else None
        report={'framework':framework,'version':version,'metadata_only_command':metadata_command,'manifests':{key:value for key,value in files.items() if key in {'package.json','package-lock.json','requirements.txt'}},'profile':profile,'acceptance_command':command,'preparations':preparations,'checks':results,'selected_count':len(selected),'positive_observed':bool(positive and worker.acceptance_observed(positive,reference,profile)),'negative':negative,'negative_observed':worker.acceptance_observed(negative,reference,profile),'assessment':verification.assess(results,[{'id':'requested','check_id':command['id'],'required':True}])}
        destination=Path('data/live-feature-frameworks')/(framework+'-'+uuid.uuid4().hex+'.json')
        destination.parent.mkdir(parents=True,exist_ok=True); destination.write_text(json.dumps(report,indent=2),encoding='utf-8')
        print('Actual framework evidence: '+str(destination))
        assert len(selected)==1 and positive['phase']=='candidate',report
        assert report['positive_observed'],'Actual selected passing target not recognized; evidence='+str(destination)
        assert not report['negative_observed'],'A skipped mapped target borrowed unrelated test evidence; artifact='+str(destination)
        assert report['assessment']['passed'],report['assessment']
    finally:
        sandbox.cancel('framework-'+framework); sandbox.cancel('framework-skipped-'+framework)
