"""Supported commands are proposals bound to an explicit execution profile."""
import hashlib
import json
import re
import shlex
from .sandbox import data, source_files

def discover(snapshot):
    files={name:content.decode('utf-8',errors='replace') if isinstance(content,bytes) else content for name,content in source_files(snapshot).items()}
    commands=[]; unknown={}
    if 'package.json' in files:
        if 'package-lock.json' not in files: return []
        package=json.loads(files['package.json'])
        for name,script in package.get('scripts',{}).items():
            if name=='benchmark':
                match=re.fullmatch(r'node (?:(--experimental-strip-types) )?([A-Za-z0-9_][A-Za-z0-9_./-]*\.(?:js|ts))',script)
                path=match[2] if match else None
                if not match or path not in files or any(part in ('','.','..') for part in path.split('/')) or bool(match[1])!=path.endswith('.ts'):
                    unknown[name]=script; continue
                commands.append({'id':name,'argv':shlex.split(script),'purpose':'benchmark','kind':'benchmark','required':True,'timeout_seconds':300,'benchmark_harness':[path]})
                continue
            purpose='test' if name=='test' else ('typecheck' if name in ('typecheck','check-types') else ('build' if name=='build' else None))
            if not purpose: unknown[name]=script; continue
            allowed=(r'node (?:--experimental-strip-types )?--test(?: [\w./*\-]+)*',r'vitest(?: run)?(?: --run)?',r'jest(?: --runInBand)?',r'tsc(?: --noEmit)?',r'vite build')
            if not any(re.fullmatch(pattern,script) for pattern in allowed): unknown[name]=script; continue
            argv=shlex.split(script)
            if argv[0]=='vitest': argv=['./node_modules/.bin/vitest','run']
            elif argv[0] in ('jest','tsc','vite'): argv[0]='./node_modules/.bin/'+argv[0]
            commands.append({'id':name,'argv':argv,'purpose':purpose,'kind':purpose,'required':True,'timeout_seconds':300})
        profile='node-npm'
    elif any(name.endswith('.py') for name in files):
        pytest=any('pytest' in files.get(name,'') for name in ('requirements.txt','uv.lock','pyproject.toml'))
        commands=[{'id':'tests','argv':['python','-m','pytest','-q'] if pytest else ['python','-m','unittest','discover','-s','tests','-v'],'purpose':'test','kind':'test','required':True,'timeout_seconds':300}]
        benchmark=next((path for path in ('benchmark.py','benchmarks/benchmark.py') if path in files),None)
        if benchmark: commands.append({'id':'benchmark','argv':['python',benchmark],'purpose':'benchmark','kind':'benchmark','required':True,'timeout_seconds':300,'benchmark_harness':[benchmark]})
        profile='python-pytest' if pytest else 'python-unittest'
    else: return []
    if not commands: return []
    plan={'profile':profile,'commands':commands,'unfamiliar_commands':unknown,'install_policy':'offline-ignore-scripts'}
    plan['fingerprint']=hashlib.sha256(json.dumps(plan,sort_keys=True).encode()).hexdigest()
    return [plan]
