import {test,expect} from '@playwright/test';
import path from 'node:path';

test.describe('real preview backend',()=>{
 test.skip(process.env.STEWARD_LIVE_UI!=='1','Requires the dedicated local preview backend; fixture tests are separate.');
 test('imports Python and TypeScript archives through tracked operations',async({page})=>{
  test.setTimeout(120000);const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto('/dashboard');
  for(const name of ['browser-python','browser-typescript']){
   await page.goto('/dashboard');await page.getByRole('button',{name:'Connect project',exact:true}).click();
   await page.getByLabel('ZIP archive').setInputFiles(path.resolve('tests/fixtures/'+name+'.zip'));
   await page.getByRole('button',{name:'Upload & index'}).click();
   await expect(page.getByRole('dialog')).toHaveCount(0,{timeout:60000});
   await expect(page.locator('.operation-progress')).toContainText('complete');
   await page.getByRole('button',{name:'Files',exact:true}).click();await expect(page.locator('.file-tree')).toContainText(name==='browser-python'?'calc.py':'calc.ts');
   await page.getByRole('button',{name:'Settings',exact:true}).click();await page.getByRole('button',{name:'Automation',exact:true}).click();await page.getByRole('button',{name:'Run now',exact:true}).click();
   await expect(page.locator('.operation-progress')).toContainText('complete',{timeout:60000});
   await expect(page.getByRole('heading',{name:'Settings',exact:true})).toBeVisible();
  }
  await page.screenshot({path:'ux-evidence/imported-typescript-desktop.png',fullPage:true});
  for(const theme of ['light','dark']){
   if(await page.locator('html').getAttribute('data-theme')!==theme)await page.getByRole('button',{name:'Toggle theme'}).click();
   for(const [width,label] of [[1440,'desktop'],[1100,'laptop'],[390,'narrow']] as const){
    await page.setViewportSize({width,height:900});await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:`ux-evidence/repository-${label}-${theme}.png`,fullPage:true});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No horizontal overflow at '+width+'px in '+theme).toBeTruthy();
   }
  }
  await page.getByRole('button',{name:'Settings',exact:true}).click();
  await expect(page.getByRole('term').filter({hasText:'Sandbox'})).toBeVisible();
  await page.screenshot({path:'ux-evidence/settings-narrow-dark.png',fullPage:true});
  expect(errors).toEqual([]);
 });
});

test('real provider configuration keeps environment overrides and viewport navigation stays fixed',async({page})=>{
 test.skip(process.env.STEWARD_LIVE_UI!=='1','Requires the dedicated preview backend.');test.setTimeout(60000);
 const repositories=await (await page.request.get('/api/repositories?summary=true')).json();
 const repo=repositories.find((entry:any)=>entry.name==='browser-typescript')||repositories[0];
 await page.goto('/#/repositories/'+repo.id+'/settings');
 await page.getByRole('button',{name:'Save provider configuration'}).click();
 await expect(page.getByText(/Environment-managed fields:.*model/)).toBeVisible();
 await page.getByRole('button',{name:'Check health'}).click();
 await expect(page.getByRole('definition').filter({hasText:'qwen-coder-3b'}).first()).toBeVisible();
 await page.screenshot({path:'ux-evidence/settings-desktop-light.png',fullPage:true});
 await page.goto('/#/repositories/'+repo.id+'/repository');
 await expect(page.getByRole('heading',{name:'Evidence-linked summaries'})).toBeVisible();
 for(const theme of ['light','dark']){
  if(await page.locator('html').getAttribute('data-theme')!==theme)await page.getByRole('button',{name:'Toggle theme'}).click();
  for(const [width,label] of [[1440,'desktop'],[1100,'laptop'],[390,'narrow']] as const){
   await page.setViewportSize({width,height:900});await page.evaluate(()=>scrollTo(0,0));
   expect((await page.locator('.sidebar').boundingBox())?.y).toBe(0);
   await page.screenshot({path:`ux-evidence/repository-${label}-${theme}.png`,fullPage:true});
   expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  }
 }
 await page.getByRole('button',{name:'Settings',exact:true}).click();await page.screenshot({path:'ux-evidence/settings-narrow-dark.png',fullPage:true});
});
test('real sample approves, revises, applies and confirms a product choice',async({page})=>{
 test.skip(process.env.STEWARD_LIVE_UI!=='1','Requires isolated backend and worker; fixture model only.');test.setTimeout(180000);
 const created=await (await page.request.post('/api/sample/reset')).json();const sample=await (await page.request.get('/api/repositories/'+created.id)).json();const jobs=await (await page.request.get('/api/jobs?summary=true')).json();const bug=jobs.find((j:any)=>j.repo_id===sample.id&&j.scenario==='bug');const feature=jobs.find((j:any)=>j.repo_id===sample.id&&j.scenario==='feature');const base='/'+sample.route_owner+'/'+sample.route_name;
 await page.goto(base+'/jobs/'+bug.id);await page.getByRole('button',{name:'Approve plan'}).click();await page.getByRole('button',{name:'Approve revision 1'}).waitFor({timeout:60000});
 const prepared=await (await page.request.get('/api/jobs/'+bug.id)).json();expect(prepared.revisions[0].diff).toContain('diff --git');expect(prepared.checks.some((c:any)=>c.phase==='regression-before'&&c.exit_code!==0)).toBeTruthy();expect(prepared.checks.some((c:any)=>c.phase==='after'&&c.exit_code===0)).toBeTruthy();
 await page.getByRole('button',{name:'Discussion',exact:true}).click();await page.getByLabel('Team note',{exact:true}).fill('Navigation verification private note');await page.getByRole('button',{name:'Add team note'}).click();await expect(page.getByText('Navigation verification private note',{exact:true})).toBeVisible();await page.getByLabel('Revision instruction').fill('Add regression coverage for zero quantity.');await page.getByRole('button',{name:'Request revision'}).click();await page.getByRole('button',{name:/^Changes/}).click();await page.getByRole('button',{name:'Approve revision 2'}).waitFor({timeout:60000});
 const revised=await (await page.request.get('/api/jobs/'+bug.id)).json();expect(revised.revisions).toHaveLength(2);expect(JSON.stringify(revised.revisions.at(-1).context)).not.toContain('Navigation verification private note');await expect(page.getByText('Target branch:')).toContainText('steward/integration');await page.getByRole('button',{name:'Approve & apply'}).click();await expect(page.locator('.heading-line .status')).toHaveText('Completed',{timeout:60000});await page.reload();await expect(page.locator('.heading-line .status')).toHaveText('Completed',{timeout:60000});
 await page.goto(base+'/jobs/'+feature.id);await page.getByRole('button',{name:'Approve plan'}).click();await page.getByRole('button',{name:'Confirm choice'}).waitFor({timeout:60000});await expect(page.getByRole('button',{name:'Confirm choice'})).toBeDisabled();await page.locator('.decision-options button').first().click();const before=await (await page.request.get('/api/jobs/'+feature.id)).json();expect(before.decisions[0].selected).toBeFalsy();await page.getByRole('button',{name:'Confirm choice'}).click();await page.getByRole('button',{name:'Approve revision 1'}).waitFor({timeout:60000});await page.getByRole('button',{name:'Back to project'}).click();await expect(page).toHaveURL(new RegExp(base+'$'));
});
