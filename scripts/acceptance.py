"""Run explicit acceptance tiers; unavailable required gates never count as passes."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlsplit
import uuid
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
if (ROOT/'vendor-current').is_dir(): sys.path.insert(0,str(ROOT/'vendor-current'))
SWITCHES=('STEWARD_LIVE_TESTS','STEWARD_LIVE_SANDBOX','STEWARD_LIVE_PIPELINE','STEWARD_LIVE_RETRIEVAL','STEWARD_LIVE_UI','STEWARD_COMPLETION_REVIEW')


def local_url(value):
    try:
        parsed=urlsplit(value)
        return bool(parsed.scheme in {'http','https'} and parsed.hostname in {'127.0.0.1','localhost','::1'} and not parsed.username and not parsed.password and parsed.path in ('','/') and not parsed.query and not parsed.fragment and parsed.port)
    except ValueError: return False


def commands(tier,output,url=None):
    output=Path(output).resolve(); gates=[]
    # STEWARD_ATLAS_REQUIRED=0 / STEWARD_AUTH_REQUIRED=0 are the test/CI escape hatches: gates (including the
    # run.py restart gate) never contact Atlas or Supabase.
    clean={**dict.fromkeys(SWITCHES,'0'),'STEWARD_ATLAS_REQUIRED':'0','STEWARD_AUTH_REQUIRED':'0','STEWARD_TEST_URL':None,'PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1',
           'PYTHONPATH':os.pathsep.join(filter(None,[str(ROOT),str(ROOT/'vendor-current'),os.environ.get('PYTHONPATH')]))}
    npm=shutil.which('npm.cmd' if os.name=='nt' else 'npm') or 'npm'
    def add(identity,group,argv,needs=(),env=None,cwd=ROOT,timeout=600,**artifacts):
        offline=dict.fromkeys(('STEWARD_ATLAS_ENABLED','STEWARD_ATLAS_GRAPH_MIRROR','STEWARD_ATLAS_RETRIEVAL'),'0') if group=='deterministic' else {}
        gates.append({'id':identity,'tier':group,'argv':argv,'cwd':str(cwd),'env':{**clean,**offline,**(env or {})},'prerequisites':list(needs),'required':True,'timeout_seconds':timeout,**artifacts})
    def pytest(identity,group,paths,needs=(),env=None,timeout=600,extra=()):
        junit=str(output/(identity+'.xml'))
        add(identity,group,[sys.executable,'-m','pytest',*paths,*extra,'-q','-p','no:cacheprovider','--junitxml='+junit],needs,env,timeout=timeout,junit=junit,allow_skips=group=='deterministic')
    def browser(identity,group,files,needs,env,extra=()):
        report=str(output/(identity+'.json'))
        add(identity,group,[npm,'run','test:browser','--',*files,'--project=chromium','--reporter=json','--output='+str(output/(identity+'-artifacts')),*extra],needs,
            {**env,'PLAYWRIGHT_JSON_OUTPUT_FILE':report},cwd=ROOT/'frontend',timeout=300,browser_report=report)
    if tier in {'deterministic','all'}:
        pytest('backend','deterministic',['tests'],('git',),extra=('--ignore=tests/live','-m','not live'))
        add('restart','deterministic',[sys.executable,'scripts/check_restart.py'],('git',),timeout=240)
        add('frontend-build','deterministic',[npm,'run','build'],('frontend',),cwd=ROOT/'frontend',timeout=180)
        browser('browser-fixtures','deterministic',['tests/navigation.spec.ts','tests/imported-workflows.spec.ts','tests/evidence.spec.ts','tests/summaries.spec.ts','tests/settings.spec.ts','tests/workspace.spec.ts','tests/global-navigation.spec.ts','tests/global-catalogue.spec.ts','tests/auth.spec.ts'],('frontend',),{})
    if tier in {'sandbox','all'}:
        pytest('sandbox','sandbox',['tests/live/test_sandbox_live.py','tests/live/test_feature_acceptance_execution.py','tests/live/test_feature_frameworks.py'],('docker',),{'STEWARD_LIVE_SANDBOX':'1'},timeout=2400)
    if tier in {'live','all'}:
        pytest('provider','live',['tests/live/test_provider_live.py'],('provider',),{'STEWARD_LIVE_TESTS':'1'},timeout=1800)
        pytest('retrieval','live',['tests/live/test_retrieval_live.py'],('provider','embedding'),{'STEWARD_LIVE_RETRIEVAL':'1'},timeout=1200)
        retrieval=str(output/'retrieval-evaluation.json')
        add('retrieval-comparison','live',[sys.executable,'-c','from scripts.evaluate_retrieval import evaluate; evaluate('+repr(retrieval)+')'],('embedding',),timeout=1200,retrieval_report=retrieval)
        pytest('imported-workflows','live',['tests/live/test_imported_workflows.py'],('provider','docker','git'),{'STEWARD_LIVE_PIPELINE':'1'},timeout=3600)
        qualification=str(output/'model-qualification.json')
        add('model-qualification','live',[sys.executable,'scripts/evaluate_models.py','--driver','scripts.qualification_driver:driver','--output',qualification],('provider','docker','git'),timeout=21660,qualification=qualification)
        backend={'STEWARD_TEST_URL':url or os.getenv('STEWARD_TEST_URL'),'STEWARD_LIVE_UI':'1'}
        browser('browser-imports','live',['tests/live-ui.spec.ts'],('frontend','backend'),backend,('--grep','imports Python and TypeScript'))
        add('browser-sample','live',[npm,'run','test:sample'],('frontend','backend'),backend,cwd=ROOT/'frontend',timeout=300)
    return gates


def probe_capabilities(gates,url):
    needs={need for gate in gates for need in gate['prerequisites']}; result={}
    if 'git' in needs: result['git']={'available':bool(shutil.which('git')),'reason':'Git executable is required'}
    if 'frontend' in needs:
        result['frontend']={'available':bool(shutil.which('node') and shutil.which('npm.cmd' if os.name=='nt' else 'npm') and (ROOT/'frontend/node_modules/@playwright/test').is_dir()),'reason':'Install Node, npm and frontend dependencies; install Playwright Chromium separately'}
    if needs & {'provider','embedding','docker'}:
        try:
            from backend import db,providers,sandbox
            db.init()
            if 'docker' in needs:
                health=sandbox.health()
                result['docker']={'available':health.get('available') is True,'reason':health.get('reason'),'runtime':health.get('runtime'),'images':health.get('images'),'probes':health.get('probes')}
            if 'provider' in needs:
                health=providers.health()
                result['provider']={'available':health.get('available') is True and health.get('mode')=='live','model':health.get('model'),'reason':health.get('error','An actual configured inference model is required')}
            if 'embedding' in needs: result['embedding']={'available':bool(providers.configuration().get('embedding_model')),'reason':'Configure an actual embedding model; the live tests verify inference'}
        except Exception as exc:
            for need in needs & {'provider','embedding','docker'}: result.setdefault(need,{'available':False,'reason':'Capability probe failed: '+type(exc).__name__})
    if 'backend' in needs:
        result['backend']={'available':False,'reason':'Start a dedicated local app and pass --url http://127.0.0.1:PORT; browser sample resets its sample data'}
        if local_url(url or ''):
            try:
                class NoRedirect(urllib.request.HTTPRedirectHandler):
                    def redirect_request(self,*args,**kwargs): return None
                with urllib.request.build_opener(NoRedirect).open(url.rstrip('/')+'/api/repositories?summary=true',timeout=5) as response:
                    result['backend']['available']=response.status==200
            except Exception as exc: result['backend']['reason']='Dedicated backend unavailable: '+type(exc).__name__
    return result


def inspect_evidence(gate):
    """A successful process alone is insufficient for test or qualification gates."""
    if gate.get('junit'):
        cases=list(ET.parse(gate['junit']).iter('testcase'))
        counts={'total':len(cases),'skipped':sum(case.find('skipped') is not None for case in cases),'failed':sum(case.find('failure') is not None or case.find('error') is not None for case in cases)}
        counts['passed']=counts['total']-counts['skipped']-counts['failed']
        status='failed' if counts['failed'] else 'blocked' if not counts['passed'] or (counts['skipped'] and not gate.get('allow_skips')) else 'passed'
        return status,{'counts':counts}
    if gate.get('browser_report'):
        report=json.loads(Path(gate['browser_report']).read_text(encoding='utf-8')); counts=report['stats']
        status='failed' if counts.get('unexpected',0) or report.get('errors') else 'blocked' if not counts.get('expected') or counts.get('skipped',0) else 'passed'
        return status,{'counts':counts}
    if gate.get('qualification'):
        report=json.loads(Path(gate['qualification']).read_text(encoding='utf-8'))
        records=report.get('results',[])
        valid=report.get('kind')=='application_qualification' and report.get('qualified') is True and len(records)==12
        status='passed' if valid else 'blocked' if records and all(item.get('status')=='blocked' for item in records) else 'failed'
        return status,{'qualified':valid,'completed':report.get('completed'),'total':report.get('total')}
    if gate.get('retrieval_report'):
        report=json.loads(Path(gate['retrieval_report']).read_text(encoding='utf-8'))
        semantic=[row for row in report['results'] if row.get('requested_mode')=='semantic']
        valid=bool(semantic and all(row.get('status')=='measured' and 'semantic' in row.get('mode','') for row in semantic) and any(row.get('embedding_calls',0)>0 for row in semantic))
        return ('passed' if valid else 'blocked'),{'semantic_tasks':len(semantic),'note':'Task/check success is measured by the separate pipeline/corpus gates'}
    return 'passed',{}


def run_command(argv,*,timeout,check=False,**options):
    """Terminate the owned command tree on deadline or controller interruption."""
    creation={'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {'start_new_session':True}
    process=subprocess.Popen(argv,**options,**creation)
    try:
        return subprocess.CompletedProcess(argv,process.wait(timeout=timeout))
    except (subprocess.TimeoutExpired,KeyboardInterrupt):
        if process.poll() is None:
            if os.name=='nt':
                subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15,check=False)
            else:
                os.killpg(process.pid,signal.SIGTERM)
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: os.killpg(process.pid,signal.SIGKILL)
            process.wait(timeout=15)
        raise


def execute(gate,capabilities,output,runner=run_command):
    result={key:gate[key] for key in ('id','tier','argv','cwd','required')}
    result.update(status='blocked',exit_code=None,artifacts=[])
    missing=[need for need in gate['prerequisites'] if not capabilities.get(need,{}).get('available')]
    if missing:
        result['reason']='; '.join(need+': '+str(capabilities.get(need,{}).get('reason','unavailable')) for need in missing)
        return result
    environment=dict(os.environ)
    for key,value in gate['env'].items():
        if value is None: environment.pop(key,None)
        else: environment[key]=value
    output=Path(output); output.mkdir(parents=True,exist_ok=True)
    log=output/(gate['id']+'.log'); result['artifacts'].append(str(log))
    begin=time.monotonic()
    try:
        with log.open('w',encoding='utf-8') as stream:
            completed=runner(gate['argv'],cwd=gate['cwd'],env=environment,stdout=stream,stderr=subprocess.STDOUT,timeout=gate['timeout_seconds'],check=False)
        result['exit_code']=completed.returncode
        result['status']='passed' if completed.returncode==0 else 'failed'
        if completed.returncode==0 or gate.get('qualification'):
            try:
                status,details=inspect_evidence(gate)
                result.update(details)
                result['status']=status if completed.returncode==0 or status!='passed' else 'failed'
            except (OSError,ValueError,KeyError,ET.ParseError) as exc:
                result.update(status='blocked' if completed.returncode==0 else 'failed',reason='Required machine-readable evidence unavailable: '+type(exc).__name__)
    except OSError as exc: result.update(status='blocked',reason='Command unavailable: '+type(exc).__name__)
    except subprocess.TimeoutExpired: result.update(status='failed',reason='Required command exceeded its deadline')
    except KeyboardInterrupt: result.update(status='failed',reason='Acceptance command interrupted; owned process tree stopped')
    result['duration_seconds']=round(time.monotonic()-begin,3)
    for key in ('junit','browser_report','qualification','retrieval_report'):
        if gate.get(key) and Path(gate[key]).is_file(): result['artifacts'].append(gate[key])
    return result


def summarize(tier,results):
    required=[result for result in results if result.get('required')]
    status='failed' if any(result['status']=='failed' for result in required) else 'blocked' if not required or any(result['status']=='blocked' for result in required) else 'passed'
    return {'schema_version':1,'tier':tier,'status':status,'recorded_at':datetime.now(timezone.utc).isoformat(),'results':results,
            'scope':'A tier pass proves only that tier. Full live release additionally requires the documented acceptance matrix and independent reviews.'}


def exit_code(report): return {'passed':0,'failed':1,'blocked':2}[report['status']]


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tier',required=True,choices=('deterministic','sandbox','live','all'))
    parser.add_argument('--output',type=Path,default=ROOT/'data/acceptance',help='Artifact parent directory; every run creates a unique subdirectory')
    parser.add_argument('--url',default=os.getenv('STEWARD_TEST_URL'),help='Dedicated loopback app for actual browser journeys; its sample data is reset')
    args=parser.parse_args(argv)
    output=args.output.resolve()/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8])
    output.mkdir(parents=True)
    # Honor the same literal project configuration as run.py; explicit environment wins.
    import run
    try:
        run.load_environment()
    except (OSError,ValueError):
        report={**summarize(args.tier,[{'id':'configuration','required':True,'status':'blocked',
            'reason':'Project .env is unreadable or invalid; correct its STEWARD_NAME=value entries.'}]),
            'artifact_directory':str(output),'complete':False}
        (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report),flush=True)
        return exit_code(report)
    gates=commands(args.tier,output,args.url); capabilities=probe_capabilities(gates,args.url)
    results=[]
    def publish():
        pending=[{'id':gate['id'],'tier':gate['tier'],'required':True,'status':'blocked','reason':'Gate has not run'} for gate in gates[len(results):]]
        report={**summarize(args.tier,[*results,*pending]),'capabilities':capabilities,'artifact_directory':str(output),'complete':len(results)==len(gates)}
        (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        return report
    report=publish()
    for gate in gates:
        results.append(execute(gate,capabilities,output))
        report=publish()
        print(json.dumps({'gate':gate['id'],'status':results[-1]['status'],'report':str(output/'report.json')}),flush=True)
        if results[-1].get('reason','').startswith('Acceptance command interrupted'): break
    print(json.dumps(report),flush=True)
    return exit_code(report)


if __name__=='__main__': raise SystemExit(main())
