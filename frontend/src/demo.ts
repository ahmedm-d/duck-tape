import type {CI,PullRequest} from './github';

export const previewMode=new URLSearchParams(location.search).get('demo')==='1';
export const previewURL='/northstar/commerce-api/reviews?demo=1';
const sha='7c92ab1f4d6e8a093b5c7d9e1f2a4b6c8d0e3f5a';
export const demoPulls:PullRequest[]=[
  {number:248,title:'Fix duplicate charges on checkout retries',author:'maya-chen',head:'fix/checkout-idempotency',base:'main',sha,draft:false,updated:'2026-11-14T09:42:00Z'},
  {number:247,title:'Add cursor pagination to the orders API',author:'alex-morgan',head:'feat/orders-pagination',base:'main',sha:'b4f2a891c3d5e7f902a4b6c8d0e1f3a5b7c9d246',draft:false,updated:'2026-11-14T09:16:00Z'},
  {number:245,title:'Upgrade Redis client and connection pooling',author:'sam-patel',head:'chore/redis-client',base:'main',sha:'e83c5d7a9b1f2046c8d0e2a4b6c9f1357a8d0e2c',draft:false,updated:'2026-11-14T08:51:00Z'},
  {number:243,title:'Trace webhook delivery across worker retries',author:'jordan-lee',head:'feat/webhook-tracing',base:'main',sha:'a6d4e219f8b7c503d1e2f4a6b8c0d9e1357a2b4c',draft:true,updated:'2026-11-13T16:32:00Z'},
];
export const demoCI:CI[]=[{state:'passed',label:'Passed',detail:'Demo: 12 checks passed.'},{state:'pending',label:'Running',detail:'Demo: integration tests running.'},{state:'failed',label:'Failed',detail:'Demo: Redis connection tests failed.'},{state:'none',label:'No checks',detail:'Demo: no checks reported.'}];
const paths=['src/server.ts','src/routes/orders.ts','src/routes/checkout.ts','src/services/orders.ts','src/services/payments.ts','src/services/idempotency.ts','src/db/client.ts','src/workers/webhooks.ts','src/config.ts','tests/orders.test.ts','tests/checkout.test.ts','tests/payments.test.ts','package.json','README.md'];
const links=[[0,1],[0,2],[0,8],[1,3],[2,4],[2,5],[3,6],[4,6],[4,5],[7,6],[7,8],[9,3],[10,2],[11,4]];
const repository={id:'preview-commerce',name:'Commerce API',route_owner:'northstar',route_name:'commerce-api',snapshot:'demo-snapshot',source_snapshot_id:'demo-snapshot',is_sample:true,branch:'main',commit:sha,origin:{source:'demo://northstar/commerce-api'},index:{snapshot:'demo-snapshot',overview:'TypeScript API for orders, payments and webhooks. PostgreSQL stores orders; Redis coordinates retries.',files:paths.map(path=>({path,status:'indexed',hash:'demo',content:path.endsWith('.ts')?`// Demo source: ${path}\nexport const service = { name: 'commerce-api' };\n`:path==='README.md'?'# Commerce API\nDemo project for the Duck Tape preview.\n':'{"name":"commerce-api","private":true}',symbols:[]})),edges:links.map(([a,b])=>({source:paths[a],target:paths[b],kind:paths[a].startsWith('tests/')?'tests':'imports',confidence:'demo fixture'})),gaps:['Illustrative data; no project was indexed.']}};
const reasons=['Checkout retries can create duplicate customer charges.','Worker restarts may deliver the same webhook twice.','Large histories need bounded queries, but existing smaller requests still work.','Redundant reads increase latency without blocking checkout.','Failed reconnect checks could interrupt order processing.','Documentation improvement with no runtime change.','Timeout recovery needs coverage before the next release.','Routine cleanup of duplicated query code.'];
const jobs=[
  {id:'preview-job-1',title:'Guard checkout requests with an idempotency key',description:'Reuse successful payment results when clients retry a checkout request.',affected_areas:['src/routes/checkout.ts','src/services/idempotency.ts','tests/checkout.test.ts'],status:'Ready for review',source:'ticket',type:'fix',priority:'high',risk:'low',age:2},
  {id:'preview-job-2',title:'Investigate webhook retries after worker restarts',description:'Trace duplicate deliveries and verify retry backoff after a worker restart.',affected_areas:['src/workers/webhooks.ts'],status:'Investigating',source:'analysis',type:'fix',priority:'high',risk:'medium',age:8},
  {id:'preview-job-3',title:'Add pagination for large order histories',description:'Add cursor pagination with a bounded page size and stable ordering.',affected_areas:['src/routes/orders.ts','src/services/orders.ts'],status:'Awaiting approval',source:'request',type:'feature',priority:'medium',risk:'low',age:25},
  {id:'preview-job-4',title:'Reduce redundant database reads in checkout',description:'Reuse loaded order data inside the payment transaction.',affected_areas:['src/services/payments.ts','src/db/client.ts'],status:'Completed',source:'analysis',type:'performance',priority:'medium',risk:'low',age:52},
  {id:'preview-job-5',title:'Update Redis client connection handling',description:'Investigate failed reconnect checks after the client version upgrade.',affected_areas:['src/config.ts','package.json'],status:'Failed',source:'security',type:'security',priority:'high',risk:'medium',age:30},
  {id:'preview-job-6',title:'Document webhook delivery guarantees',description:'Document retry intervals, signature validation and at-least-once delivery.',affected_areas:['README.md'],status:'Proposed',source:'request',type:'feature',priority:'low',risk:'low',age:77},
  {id:'preview-job-7',title:'Cover payment timeout recovery',description:'Add regression coverage for timeouts before the payment response arrives.',affected_areas:['tests/payments.test.ts'],status:'Verifying',source:'ticket',type:'fix',priority:'medium',risk:'low',age:100},
  {id:'preview-job-8',title:'Simplify order query helpers',description:'Consolidate repeated query parameters while preserving existing results.',affected_areas:['src/services/orders.ts'],status:'Completed',source:'analysis',type:'refactor',priority:'low',risk:'low',age:145},
].map(({age,...job},index)=>({...job,priority_reason:'Demo assessment: '+reasons[index],risk_reason:job.risk==='medium'?'Demo assessment: changes affect retries or connection handling; check recovery paths.':'Demo assessment: a bounded change in the listed files; targeted regression checks are needed.',investigation:{summary:'Illustrative investigation: '+job.description,evidence:job.affected_areas.map(path=>({path,start_line:1,end_line:1})),missing:['Demo data only; no live investigation was performed.']},repo_id:repository.id,base_snapshot:repository.snapshot,created_at:new Date(Date.now()-age*3600000).toISOString()}));

export function demoResponse(path:string,method:string):unknown {
  if(method!=='GET')throw new Error('Preview is read-only. Exit preview to change a real project.');
  const route=path.split('?')[0];
  if(route==='/repositories')return [repository];
  if(route==='/repositories/'+repository.id)return repository;
  if(route==='/jobs')return jobs;
  if(route.startsWith('/jobs/'))return jobs.find(job=>job.id===route.split('/')[2])||{};
  if(route==='/knowledge')return [{id:'preview-decision',version:1,title:'Idempotent checkout',content:'Reuse the payment result for repeated requests with the same idempotency key.',scope:'src/services/payments.ts',status:'observed',category:'architecture',evidence:['src/services/idempotency.ts'],last_validated_snapshot:'demo-snapshot'}];
  if(route==='/activity')return [{id:'preview-event',repo_id:repository.id,message:'Demo agent prepared checkout retry fix',created_at:'2026-11-14T09:42:00Z'}];
  if(route==='/settings')return {providers:{available:false,qualification:'Demo only'},sandbox:{available:false,reason:'Read-only preview'}};
  return [];
}
