"""Report actual local capabilities without revealing provider credentials."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'vendor-current'))


def diagnose():
    from backend import db,providers,sandbox
    db.init()
    git=shutil.which('git')
    version=subprocess.run([git,'--version'],capture_output=True,text=True,check=True).stdout.strip() if git else None
    with db.connect() as con:
        migration=con.execute('SELECT MAX(version) FROM migrations').fetchone()[0]
        integrity=con.execute('PRAGMA quick_check').fetchone()[0]
    inference=providers.health()
    execution=sandbox.health()
    frontend=(ROOT/'frontend'/'dist'/'index.html').is_file()
    core=bool(git and frontend and integrity=='ok')
    return {'recorded_at':db.now(),'core_ready':core,'python':sys.version.split()[0],'git':version,'frontend_built':frontend,
        'database':{'schema':migration,'integrity':integrity},'provider':inference,'sandbox':execution,
        'mode':'live provider and sandbox available; model qualification is a separate acceptance gate' if inference['available'] and execution['available'] else 'offline sample and analysis',
        'limits':['Local demo identity is not production authentication','Model availability does not establish coding quality','Unsupported package managers remain analysis/export only']}


def main():
    from run import load_environment
    load_environment()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--require-live',action='store_true')
    args=parser.parse_args()
    result=diagnose()
    text=json.dumps(result,indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(text,encoding='utf-8')
    print(text)
    return 0 if result['core_ready'] and (not args.require_live or result['provider']['available'] and result['sandbox']['available']) else 1


if __name__=='__main__': raise SystemExit(main())
