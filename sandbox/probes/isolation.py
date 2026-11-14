"""Trusted active isolation checks. Executed only in a Steward-owned image."""
import ctypes
import json
import os
from pathlib import Path
import socket
import subprocess

checks={}
checks['nonroot']=os.getuid()==10001 and os.getgid()==10001
def inaccessible(name):
    try:
        with open(name, 'rb') as source: source.read(1)
        return False
    except OSError: return True
checks['no_credentials']=all(inaccessible(p) for p in ['/root/.aws/credentials','/root/.ssh/id_rsa','/var/run/docker.sock','/run/docker.sock','/host/steward-host-canary'])
checks['clean_environment']=not any(any(word in k.upper() for word in ['TOKEN','SECRET','PASSWORD','AWS_','AZURE_']) for k in os.environ)
try:
    socket.create_connection(('1.1.1.1',443),timeout=1).close()
    checks['no_network']=False
except OSError: checks['no_network']=True
try:
    Path('/steward-write-probe').write_text('bad')
    checks['readonly_root']=False
except OSError: checks['readonly_root']=True
checks['no_privilege_escalation']=ctypes.CDLL(None).setuid(0)!=0
status=Path('/proc/self/status').read_text()
checks['no_new_privileges']='NoNewPrivs:\t1' in status
checks['no_capabilities']='CapEff:\t0000000000000000' in status
checks['memory_limit']=Path('/sys/fs/cgroup/memory.max').read_text().strip()=='2147483648'
checks['process_limit']=Path('/sys/fs/cgroup/pids.max').read_text().strip()=='256'
checks['cpu_limit']=Path('/sys/fs/cgroup/cpu.max').read_text().strip()=='200000 100000'
print(json.dumps(checks,sort_keys=True))
