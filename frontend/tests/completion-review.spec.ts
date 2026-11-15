import {test,expect,Page} from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import {fixture} from './fixtures';

test.describe.configure({mode:'default'});
test.skip(process.env.STEWARD_COMPLETION_REVIEW!=='1','Dedicated preview review only. No live inference is requested.');
test.beforeEach(async({baseURL,page})=>{
 expect(baseURL).toBe('http://127.0.0.1:8767');
 if(!sample&&fs.existsSync(path.join(output,'sample-results.json'))){
  const recorded=JSON.parse(fs.readFileSync(path.join(output,'sample-results.json'),'utf8'));
  sample=await (await page.request.get('/api/repositories/'+recorded.repo)).json();
  const jobs=await (await page.request.get('/api/jobs?summary=true')).json();
  bug=jobs.find((job:any)=>job.id===recorded.job);feature=jobs.find((job:any)=>job.repo_id===sample.id&&job.scenario==='feature');
 }
});
const output='ux-evidence/completion-review';
const capture=async(page:Page,name:string)=>{fs.mkdirSync(output,{recursive:true});await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:path.join(output,name+'.png'),fullPage:true})};
let sample:any,bug:any,feature:any;

test('actual imported source, graph, themes, widths and unchanged provider settings',async({page})=>{
 test.setTimeout(90000);const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
 const repositories=await (await page.request.get('/api/repositories?summary=true')).json();
 const imported=repositories.find((repo:any)=>repo.name==='browser-python');expect(imported).toBeTruthy();
 const detail=await (await page.request.get('/api/repositories/'+imported.id)).json();
 await page.goto('/#/repositories/'+imported.id+'/repository');
 await expect(page.getByText('File coverage describes indexing, not semantic understanding. Dynamic relationships may be incomplete.')).toBeVisible();
 await page.getByRole('button',{name:'Files',exact:true}).click();await page.getByRole('button',{name:'calc.py Python',exact:true}).click();
 await expect(page.locator('.source-code')).toContainText('def');
 await expect(page.locator('.edges')).toContainText(detail.index.edges[0].source);
 await page.getByRole('button',{name:'Overview',exact:true}).click();await expect(page.getByRole('heading',{name:'Evidence-linked summaries'})).toBeVisible();
 for(const theme of ['light','dark']){
  if(await page.locator('html').getAttribute('data-theme')!==theme)await page.getByRole('button',{name:'Toggle theme'}).click();
  for(const width of [1440,1100,390]){
   await page.setViewportSize({width,height:900});await page.evaluate(()=>scrollTo(0,0));
   expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
   expect((await page.locator('.sidebar').boundingBox())?.y).toBe(0);
   await capture(page,`source-${width}-${theme}`);
  }
 }
 await page.setViewportSize({width:1440,height:1000});await page.getByRole('button',{name:'Settings',exact:true}).click();
 await page.getByRole('button',{name:'Save provider configuration'}).click();
 await expect(page.getByText(/Environment-managed fields:/)).toBeVisible();
 await page.getByRole('button',{name:'Check health'}).click();
 await expect(page.getByRole('status').filter({hasText:'Provider health refreshed'})).toBeVisible();
 await expect(page.getByRole('term').filter({hasText:'Qualification'})).toBeVisible();
 await capture(page,'settings-desktop-dark');
 await page.setViewportSize({width:390,height:900});await capture(page,'settings-narrow-dark');
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
 expect(errors).toEqual([]);
});

test('fresh sample: real checks, exports, revision, note isolation, approval and managed apply',async({page})=>{
 test.setTimeout(150000);const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
 const response=await page.request.post('/api/sample/reset');expect(response.ok()).toBeTruthy();sample=await response.json();
 const jobs=await (await page.request.get('/api/jobs?summary=true')).json();
 bug=jobs.find((job:any)=>job.repo_id===sample.id&&job.scenario==='bug');feature=jobs.find((job:any)=>job.repo_id===sample.id&&job.scenario==='feature');
 await page.goto('/#/repositories/'+sample.id+'/jobs');
 await page.locator('.job-filter-options summary').click();await page.getByLabel('Filter source').selectOption(bug.source);await page.getByLabel('Filter type').selectOption(bug.type);await page.getByLabel('Filter priority').selectOption(bug.priority);
 await page.getByLabel('Search jobs').fill(bug.title);await expect(page.locator('.job-row')).toHaveCount(1);
 await page.locator('.job-row').click();await expect(page).toHaveURL(new RegExp(bug.id+'$'));await page.reload();
 await expect(page.getByRole('heading',{name:bug.title})).toBeVisible();
 await page.getByRole('button',{name:'Approve plan'}).click();await page.getByRole('button',{name:'Approve revision 1'}).waitFor({timeout:60000});
 const prepared=await (await page.request.get('/api/jobs/'+bug.id)).json();
 expect(prepared.revisions[0].diff).toContain('diff --git');expect(prepared.revisions[0].provider).toBeTruthy();
 expect(prepared.checks.some((check:any)=>check.phase==='regression-before'&&check.exit_code!==0)).toBeTruthy();
 expect(prepared.checks.some((check:any)=>check.phase==='after'&&check.exit_code===0)).toBeTruthy();
 for(const [label,extension] of [['Export patch','.patch'],['Changed files ZIP','.zip']]){
  const downloadEvent=page.waitForEvent('download');await page.getByRole('link',{name:label}).click();const download=await downloadEvent;
  expect(download.suggestedFilename()).toContain(extension);if(extension==='.patch')expect(fs.readFileSync((await download.path())!,'utf8')).toContain('diff --git');
 }
 await page.getByRole('button',{name:'Split view'}).click();await expect(page.getByText('Before',{exact:true}).first()).toBeVisible();await page.getByRole('button',{name:'Unified view'}).click();
 await page.getByRole('button',{name:'Checks',exact:true}).click();await expect(page.getByRole('heading',{name:'Regression before change'})).toBeVisible();
 await page.locator('.check').first().click();await expect(page.locator('.check').first().locator('pre')).toBeVisible();
 const evidence=page.locator('.evidence-list .evidence').first();await evidence.click();await expect(page.getByRole('dialog',{name:'Captured source evidence'})).toBeVisible();
 await page.keyboard.press('Tab');expect(await page.evaluate(()=>!!document.activeElement?.closest('[role=dialog]'))).toBeTruthy();await page.keyboard.press('Escape');await expect(evidence).toBeFocused();
 await page.getByRole('button',{name:'Discussion',exact:true}).click();
 await page.getByLabel('Team note',{exact:true}).fill('Completion review private note.');await page.getByRole('button',{name:'Add team note'}).click();
 await expect(page.getByText('Completion review private note.',{exact:true})).toBeVisible();
 await page.getByLabel('Revision instruction').fill('Add regression coverage for zero quantity.');await page.getByRole('button',{name:'Request revision'}).click();
 await page.getByRole('button',{name:/^Changes/}).click();await page.getByRole('button',{name:'Approve revision 2'}).waitFor({timeout:60000});
 const revised=await (await page.request.get('/api/jobs/'+bug.id)).json();expect(revised.revisions).toHaveLength(2);expect(revised.approval).toBeNull();expect(JSON.stringify(revised.revisions[1].context)).not.toContain('Completion review private note.');
 await page.getByRole('button',{name:'Approve revision 2'}).click();
 await page.getByLabel('Patch revision').selectOption(revised.revisions[0].id);
 await expect(page.locator('.job-summary')).toContainText('Revision 2 approved');
 await capture(page,'approved-revision-label');
 await page.getByLabel('Patch revision').selectOption(revised.revisions[1].id);
 await page.getByRole('button',{name:'Approve & apply'}).click();await expect(page.locator('.heading-line .status')).toHaveText('Completed');
 await capture(page,'sample-completed');
 const completed=await (await page.request.get('/api/jobs/'+bug.id)).json();expect(completed.status).toBe('Completed');
 fs.writeFileSync(path.join(output,'sample-results.json'),JSON.stringify({repo:sample.id,job:bug.id,revision:completed.current_revision,approval:completed.approval,provider:completed.revisions.at(-1).provider,checks:completed.checks.map((check:any)=>({phase:check.phase,status:check.status,exit:check.exit_code}))},null,2));
 expect(errors).toEqual([]);
});

test('fresh sample product decision and versioned knowledge approval',async({page})=>{
 test.setTimeout(120000);expect(sample).toBeTruthy();
 await page.goto('/#/repositories/'+sample.id+'/jobs/'+feature.id);
 if(feature.status==='Awaiting approval'){
  await page.getByRole('button',{name:'Approve plan'}).click();
  await expect(page.getByText('YOUR DECISION IS NEEDED',{exact:true})).toBeVisible({timeout:60000});
  await page.locator('.decision-options button').first().click();await page.getByRole('button',{name:'Confirm choice'}).click();
 }
 await page.getByRole('button',{name:'Approve revision 1'}).waitFor({timeout:60000});
 await capture(page,'sample-decision-prepared');
 // Seed an agent-supplied decision; the UI no longer creates blank knowledge entries.
 const supplied=await page.request.post('/api/knowledge',{data:{repo_id:sample.id,title:'Completion review rule',content:'Preserve quantity validation.',scope:'repository',category:'decision',status:'observed',evidence:'tests/test_quote.py'}});expect(supplied.ok()).toBeTruthy();
 await page.getByRole('button',{name:'Project knowledge',exact:true}).click();await page.getByRole('button',{name:'Decisions',exact:true}).click();
 const card=page.locator('.knowledge-card').filter({hasText:'Completion review rule'});await expect(card).toContainText('Version 1');
 await card.getByRole('button',{name:'Approve version 1'}).click();await expect(card).toContainText('team-approved');
 const knowledge=await (await page.request.get('/api/knowledge?repo_id='+sample.id)).json();const entry=knowledge.find((item:any)=>item.title==='Completion review rule');
 expect(entry.status).toBe('team-approved');expect(entry.last_validated_snapshot).toBe(sample.snapshot);
 await card.getByRole('button',{name:'Version history'}).click();await expect(page.getByRole('dialog',{name:'Knowledge version history'})).toContainText('Version');await page.getByRole('button',{name:'Close history'}).click();
 await capture(page,'knowledge-approved');
});

test('approval label stays bound to the approved revision while viewing earlier changes',async({page})=>{
 const detail=await (await page.request.get('/api/jobs/'+bug.id)).json();expect(detail.revisions).toHaveLength(2);
 await page.goto('/#/repositories/'+sample.id+'/jobs/'+bug.id);
 await page.getByLabel('Patch revision').selectOption(detail.revisions[0].id);
 await capture(page,'approval-label-regression');
 await expect(page.locator('.job-summary')).toContainText('Revision 2 approved');
 for(const theme of ['light','dark']){
  if(await page.locator('html').getAttribute('data-theme')!==theme)await page.getByRole('button',{name:'Toggle theme'}).click();
  for(const width of [1440,1100,390]){
   await page.setViewportSize({width,height:900});
   expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
   await capture(page,`job-${width}-${theme}`);
  }
 }
});

test('invalid archive exposes whether import errors are visible inside the dialog',async({page})=>{
 await page.goto('/#/repositories/'+sample.id+'/repository');await page.getByRole('button',{name:'Connect project',exact:true}).click();
 await page.getByLabel('ZIP archive').setInputFiles({name:'invalid-review.zip',mimeType:'application/zip',buffer:Buffer.from('not a ZIP archive')});
 await page.getByRole('button',{name:'Upload & index'}).click();
 await expect(page.locator('.operation-progress')).toContainText('failed',{timeout:20000});
 await expect(page.getByRole('dialog')).toHaveCount(0);
 await expect(page.locator('.operation-progress')).toContainText('Invalid ZIP archive');
 await page.getByRole('button',{name:'Retry operation'}).click();await expect(page.locator('.operation-progress')).toContainText('failed');
 await capture(page,'import-error-after-closing-dialog');
});

test('activity links preserve the job repository context',async({page})=>{
 const events=await (await page.request.get('/api/activity')).json();const eventIndex=events.filter((event:any)=>event.repo_id===sample.id).findIndex((event:any)=>event.job_id===bug.id);expect(eventIndex).toBeGreaterThanOrEqual(0);
 await page.goto('/'+sample.route_owner+'/'+sample.route_name+'/activity');
 const event=page.locator('.activity-timeline>div').nth(eventIndex);
 await event.getByRole('button',{name:'Open job'}).click();await expect(page.getByRole('heading',{name:bug.title})).toBeVisible();
 await capture(page,'activity-repository-context');
 await expect(page).toHaveURL(new RegExp('/'+sample.route_owner+'/'+sample.route_name+'/jobs/'+bug.id+'$'));
 await expect(page.getByLabel('Switch project')).toHaveValue(sample.id);
});

test('fixture import rejection stays visible and accepted progress can be cancelled',async({page})=>{
 await fixture(page);const operation={id:'review-operation',operation_id:'review-operation',kind:'upload',status:'queued',checkpoint:'fetching'};let cancelled=false;
 await page.route('**/api/repositories/import',route=>route.fulfill({status:422,json:{detail:'Import validation failed'}}));
 await page.route('**/api/repositories/upload',route=>route.fulfill({status:202,json:operation}));
 await page.route('**/api/operations/review-operation',route=>route.fulfill({json:{...operation,status:cancelled?'cancelled':'queued'}}));
 await page.route('**/api/operations/review-operation/cancel',async route=>{cancelled=true;await route.fulfill({json:{...operation,status:'cancelled'}})});
 await page.goto('/#/repositories/repo-one/repository');await page.getByRole('button',{name:'Connect project',exact:true}).click();
 await page.getByLabel('Public HTTPS Git URL').fill('https://example.invalid/repository.git');await page.getByRole('button',{name:'Import Git project'}).click();
 await expect(page.getByRole('dialog').getByRole('alert')).toHaveText('Import validation failed');
 await page.getByLabel('ZIP archive').setInputFiles({name:'fixture.zip',mimeType:'application/zip',buffer:Buffer.from('fixture response; not actual archive')});
 await page.getByRole('button',{name:'Upload & index'}).click();await expect(page.getByRole('dialog')).toHaveCount(0);
 await page.getByRole('button',{name:'Cancel operation'}).click();await expect.poll(()=>cancelled).toBe(true);
 await expect(page.locator('.operation-progress')).toContainText('cancelled');
});
