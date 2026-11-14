import {Page,expect} from '@playwright/test';
export const repositories=[{id:'repo-one',name:'Python project',snapshot:'snapshot-one',is_sample:false},{id:'repo-two',name:'TypeScript project',snapshot:'snapshot-two',is_sample:false}];
export const jobs=[{id:'job-one',repo_id:'repo-one',title:'Fix quoted prices',description:'Preserve integer cents',status:'Ready for review',source:'ticket',type:'fix',priority:'high',risk:'low',base_snapshot:'snapshot-one',current_revision:'rev-one'},{id:'job-two',repo_id:'repo-one',title:'Dependency advice',status:'Proposed',source:'security',type:'security',priority:'medium',risk:'high'}];
export async function fixture(page:Page){
 const calls:string[]=[];
 await page.route('**/api/**',async route=>{
  const url=new URL(route.request().url()); const path=url.pathname.slice(4); calls.push(path);
  let body:any=[];
  if(path==='/repositories')body=repositories;
  else if(path==='/jobs')body=jobs;
  else if(path==='/settings'||path==='/settings/health')body={providers:{mode:'analysis-only',qualified:false,reason:'No qualified model'},sandbox:{available:false,reason:'Docker unavailable'}};
  else if(path==='/repositories/repo-one'||path==='/repositories/repo-two')body={...repositories.find(r=>path.endsWith(r.id)),index:{snapshot:path.endsWith('repo-one')?'snapshot-one':'snapshot-two',overview:'Indexed project',files:[{path:'pricing.py',status:'indexed',hash:'hash',content:'def total():\n    return 20\n'}],edges:[],gaps:['Dynamic calls are incomplete']}};
  else if(path==='/jobs/job-one')body={...jobs[0],checks:[{id:'baseline',revision_id:'rev-one',phase:'baseline',status:'passed',command:['python','-m','unittest'],stdout:'Ran 1 test\nOK'},{id:'candidate',revision_id:'rev-one',phase:'candidate',status:'failed',command:['python','-m','unittest'],stdout:'AssertionError: expected integer cents'}],revisions:[{id:'rev-one',number:1,base_snapshot:'snapshot-one',patch_hash:'patch',explanation:'Fix total',files:[{path:'pricing.py',before:'def total():\n    return 10\n',after:'def total():\n    return 20\n'}],diff:'diff --git a/pricing.py b/pricing.py\n--- a/pricing.py\n+++ b/pricing.py\n@@ -1,2 +1,2 @@\n def total():\n-    return 10\n+    return 20\n',context:{items:[{path:'pricing.py',start_line:2,end_line:2,content:'    return 10',snapshot:'snapshot-one'}]}}]};
  else if(path.startsWith('/knowledge'))body=[{id:'knowledge-one',repo_id:'repo-one',version:2,title:'Use cents',content:'Keep integer cents',status:'observed',scope:'pricing.py',last_validated_snapshot:'snapshot-one',evidence:[]}];
  await route.fulfill({json:body});
 });
 return calls;
}
