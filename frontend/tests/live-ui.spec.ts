import {test,expect} from '@playwright/test';
import path from 'node:path';

test.describe('real preview backend',()=>{
 test.skip(process.env.STEWARD_LIVE_UI!=='1','Requires the dedicated local preview backend; fixture tests are separate.');
 test('imports Python and TypeScript archives through tracked operations',async({page})=>{
  test.setTimeout(120000);const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto('/');await page.getByRole('button',{name:'Repository',exact:true}).click();
  for(const name of ['browser-python','browser-typescript']){
   await page.getByRole('button',{name:'Import repository',exact:true}).click();
   await page.getByLabel('ZIP archive').setInputFiles(path.resolve('tests/fixtures/'+name+'.zip'));
   await page.getByRole('button',{name:'Upload & index'}).click();
   await expect(page.getByRole('dialog')).toHaveCount(0,{timeout:60000});
   await expect(page.locator('.operation-progress')).toContainText('complete');
   await expect(page.locator('.file-tree')).toContainText(name==='browser-python'?'calc.py':'calc.ts');
   await page.getByRole('button',{name:'Run now',exact:true}).click();
   await expect(page.locator('.operation-progress')).toContainText('complete',{timeout:60000});
   await expect(page.getByRole('heading',{name:'Repository',exact:true})).toBeVisible();
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
 await page.getByRole('button',{name:'Repository',exact:true}).click();
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
