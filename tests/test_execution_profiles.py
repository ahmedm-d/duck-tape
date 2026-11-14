import json
import pytest
from backend import execution_profiles


@pytest.mark.parametrize('path',['benchmark.py','benchmarks/benchmark.py'])
def test_existing_python_benchmark_is_discovered_and_bound_to_harness(path):
    plan=execution_profiles.discover({'files':{path:'print("benchmark")'}})[0]
    benchmark=next(command for command in plan['commands'] if command['purpose']=='benchmark')
    assert benchmark['argv']==['python',path]
    assert benchmark['benchmark_harness']==[path] and benchmark['required']


@pytest.mark.parametrize(('script','path','argv'),[
    ('node benchmarks/run.js','benchmarks/run.js',['node','benchmarks/run.js']),
    ('node --experimental-strip-types benchmark.ts','benchmark.ts',['node','--experimental-strip-types','benchmark.ts']),
])
def test_existing_node_benchmark_accepts_only_exact_supported_argv(script,path,argv):
    files={'package.json':json.dumps({'scripts':{'test':'node --test','benchmark':script}}),'package-lock.json':'{"lockfileVersion":3,"packages":{}}',path:'// benchmark'}
    plan=execution_profiles.discover({'files':files})[0]
    benchmark=next(command for command in plan['commands'] if command['purpose']=='benchmark')
    assert benchmark['argv']==argv and benchmark['benchmark_harness']==[path]


@pytest.mark.parametrize('script',[
    'node ../benchmark.js','node /benchmark.js','node missing.js','node benchmark.js && echo fast',
    'node --eval benchmark.js','node benchmark.ts','node --experimental-strip-types benchmark.js',
])
def test_unsupported_benchmark_is_unfamiliar_and_never_expanded(script):
    files={'package.json':json.dumps({'scripts':{'test':'node --test','benchmark':script}}),'package-lock.json':'{"lockfileVersion":3,"packages":{}}','benchmark.js':'// benchmark','benchmark.ts':'// benchmark'}
    plan=execution_profiles.discover({'files':files})[0]
    assert not any(command['purpose']=='benchmark' for command in plan['commands'])
    assert plan['unfamiliar_commands']['benchmark']==script
