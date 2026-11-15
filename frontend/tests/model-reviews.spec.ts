import {test,expect} from '@playwright/test';
import {fixture,repositories} from './fixtures';

test('security reviews persist, show hypotheses and never claim execution',async({page})=>{
 await fixture(page);let rows:any[]=[];
 await page.route('**/api/repositories/repo-one/model-reviews',route=>route.fulfill({json:rows}));
 await page.route('**/api/repositories/repo-one/security-review',route=>{
  rows=[{id:'review',status:'complete',payload:{kind:'security',snapshot_id:'snapshot-one'},result:{summary:'Potential unsafe evaluation',provider:[{model:'mock-Claude'}],findings:[{severity:'high',message:'Untrusted input may reach eval',verification:'Exercise input in sandbox',reference:{path:'app.py',start_line:2,end_line:2},evidence:{start_line:1,content:'def parse(x):\n return eval(x)'}}],limitations:['Not confirmed exploitable'],context:{items:[{path:'app.py'}]}}}];
  return route.fulfill({json:rows[0]});
 });
 await page.goto('/local/repo-one/reviews');await page.getByRole('button',{name:'Model reviews',exact:true}).click();
 await page.getByRole('button',{name:'Review source security'}).click();
 await expect(page.getByText('Potential unsafe evaluation',{exact:true})).toBeVisible();
 await expect(page.getByText('Models: mock-Claude · No tests executed')).toBeVisible();
 await page.getByText('high · hypothesis · Untrusted input may reach eval').click();
 await expect(page.getByText('return eval(x)',{exact:true})).toBeVisible();
 await page.reload();await page.getByRole('button',{name:'Model reviews',exact:true}).click();
 await expect(page.getByText('Potential unsafe evaluation',{exact:true})).toBeVisible();
});

test('failed and active reviews expose retry and cancellation',async({page})=>{
 await fixture(page);
 await page.route('**/api/repositories/repo-one/model-reviews',route=>route.fulfill({json:[{id:'failed',status:'failed',payload:{kind:'security'},error:'Provider timeout'},{id:'active',status:'running',payload:{kind:'pr',number:2}}]}));
 const actions:string[]=[];await page.route('**/api/operations/**',route=>{actions.push(new URL(route.request().url()).pathname);return route.fulfill({json:{}})});
 await page.goto('/local/repo-one/reviews');await page.getByRole('button',{name:'Model reviews',exact:true}).click();
 await page.getByRole('button',{name:'Retry review'}).click();await page.getByRole('button',{name:'Cancel review'}).click();
 expect(actions).toEqual(['/api/operations/failed/retry','/api/operations/active/cancel']);
});


test('PR review submits only the selected repository number and exact head',async({page})=>{
 await fixture(page);const sha='a'.repeat(40);let posted:any;
 await page.route('**/api/repositories?summary=true',route=>route.fulfill({json:[{...repositories[0],origin:{source:'https://github.com/acme/service.git'}}]}));
 await page.route('https://api.github.com/**',route=>route.fulfill({json:new URL(route.request().url()).pathname.endsWith('/pulls')?[{number:42,title:'Fix retry',user:{login:'dev'},head:{ref:'fix',sha},base:{ref:'main'},draft:false,updated_at:'2026-11-15T00:00:00Z'}]:{check_runs:[],total_count:0,statuses:[],sha}}));
 await page.route('**/api/repositories/repo-one/model-reviews',route=>route.fulfill({json:[]}));
 await page.route('**/api/repositories/repo-one/pr-review',route=>{posted=route.request().postDataJSON();return route.fulfill({json:{id:'review'}})});
 await page.goto('/local/repo-one/reviews');await page.getByRole('button',{name:'AI review #42',exact:true}).click();
 await page.getByRole('button',{name:'Review PR #42',exact:true}).click();
 await expect.poll(()=>posted).toEqual({number:42,head:sha});
});
