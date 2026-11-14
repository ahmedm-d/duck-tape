const fs=require('fs'),net=require('net');
(async()=>{
 const read=p=>fs.readFileSync(p,'utf8').trim();
 const inaccessible=p=>{try{fs.readFileSync(p);return false;}catch{return true;}};
 const checks={nonroot:process.getuid()===10001&&process.getgid()===10001,no_credentials:['/root/.aws/credentials','/root/.ssh/id_rsa','/var/run/docker.sock','/run/docker.sock','/host/steward-host-canary'].every(inaccessible),clean_environment:!Object.keys(process.env).some(k=>/TOKEN|SECRET|PASSWORD|AWS_|AZURE_/.test(k.toUpperCase())),memory_limit:read('/sys/fs/cgroup/memory.max')==='2147483648',process_limit:read('/sys/fs/cgroup/pids.max')==='256',cpu_limit:read('/sys/fs/cgroup/cpu.max')==='200000 100000'};
 checks.no_new_privileges=/NoNewPrivs:\s+1/.test(read('/proc/self/status'));
 checks.no_capabilities=/CapEff:\s+0000000000000000/.test(read('/proc/self/status'));
 try{process.setuid(0);checks.no_privilege_escalation=false;}catch{checks.no_privilege_escalation=true;}
 try{fs.writeFileSync('/steward-write-probe','bad');checks.readonly_root=false;}catch{checks.readonly_root=true;}
 checks.no_network=await new Promise(resolve=>{const s=net.connect({host:'1.1.1.1',port:443});s.setTimeout(1000);s.on('connect',()=>{s.destroy();resolve(false)});s.on('error',()=>resolve(true));s.on('timeout',()=>{s.destroy();resolve(true)});});
 console.log(JSON.stringify(checks));
})();
