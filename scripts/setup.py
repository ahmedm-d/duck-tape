"""Create a local Python environment and build the locked frontend."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sandbox',action='store_true',help='Also build the two pinned local Docker runtime images')
    args=parser.parse_args()
    environment=ROOT/'.venv'
    python=environment/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    # Re-running venv also repairs an interrupted pip bootstrap.
    subprocess.run([sys.executable,'-m','venv',str(environment)],check=True,cwd=ROOT)
    subprocess.run([str(python),'-m','pip','install','-r','requirements.lock'],check=True,cwd=ROOT)
    npm=shutil.which('npm.cmd' if os.name=='nt' else 'npm')
    if not npm: raise SystemExit('Node/npm is required to build the frontend')
    for argv in ([npm,'ci'],[npm,'run','build']):
        subprocess.run(argv,check=True,cwd=ROOT/'frontend')
    if args.sandbox:
        for language in ('python','node'):
            subprocess.run(['docker','build','--network=none','-t','steward-'+language+':1','-f','sandbox/images/'+language+'.Dockerfile','.'],check=True,cwd=ROOT)
    print('Ready. Start with: '+str(python)+' run.py')


if __name__=='__main__': main()
