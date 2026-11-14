import {test,expect} from '@playwright/test';
import {fixture} from './fixtures';
import fs from 'node:fs';

for(const previousRevision of [false,true])test(`unverified candidate exports exact bytes ${previousRevision?'alongside an earlier revision':'before any revision'}`,async({page})=>{
 await fixture(page);
 const diff='diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n';
 await page.route('**/api/jobs/job-one',route=>route.fulfill({json:{id:'job-one',repo_id:'repo-one',title:'Unverified change',status:'Failed',source:'request',type:'fix',priority:'high',error:'Docker is unavailable',current_revision:previousRevision?'old-revision':undefined,revisions:previousRevision?[{id:'old-revision',number:1,diff:'old captured diff',files:[],explanation:'Previous verified revision'}]:[],checks:[],runs:[{id:'run-one',status:'failed',base_snapshot:'snapshot-one',candidate_patch_hash:'actual-hash',candidate_diff:diff,candidate_files:[{path:'app.py',before:'value = 1\n',after:'value = 2\n'}]}]}}));
 await page.goto('/#/repositories/repo-one/jobs/job-one');
 if(previousRevision)await page.getByRole('button',{name:'Runs',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Unverified candidate'})).toBeVisible();
 await expect(page.getByLabel('Patch diff')).toContainText('value = 2');
 await expect(page.getByRole('button',{name:/Approve revision|Apply to managed branch/})).toHaveCount(0);
 const download=page.waitForEvent('download');await page.getByRole('button',{name:'Export unverified patch'}).click();
 const result=await download;expect(result.suggestedFilename()).toBe('duck-tape-unverified-run-one.patch');
 expect(fs.readFileSync((await result.path())!,'utf8')).toBe(diff);
 await page.getByRole('button',{name:'Split view',exact:true}).click();
 await expect(page.locator('.split-diff')).toContainText('value = 1');
});

test('failed candidate checks and exact captured evidence remain inspectable',async({page})=>{
 await fixture(page);await page.goto('/#/repositories/repo-one/jobs/job-one');
 await expect(page.getByRole('heading',{name:'Fix quoted prices'})).toBeVisible();
 await page.getByRole('button',{name:'Evidence',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Candidate checks'})).toBeVisible();
 await page.locator('details').filter({hasText:'candidate'}).first().click();
 await expect(page.getByText('AssertionError: expected integer cents',{exact:true})).toBeVisible();
 await page.locator('.evidence').first().click();
 await expect(page.locator('[data-source-line="2"]')).toHaveClass(/cited-line/);
});

test('knowledge approval names the version and snapshot being reviewed',async({page})=>{
 await fixture(page);let approval:any;
 await page.route('**/api/knowledge/knowledge-one/approve',async route=>{approval=route.request().postDataJSON();await route.fulfill({json:{id:'knowledge-one',version:3,status:'team-approved'}})});
 await page.goto('/#/repositories/repo-one/knowledge');
 await page.getByRole('button',{name:'Approve version 2'}).click();
 await expect.poll(()=>approval).toEqual({version:2,snapshot:'snapshot-one'});
});

test('switching repositories discards a pending answer from the previous snapshot',async({page})=>{
 await fixture(page);let release:()=>void=()=>{};const waiting=new Promise<void>(resolve=>release=resolve);
 await page.route('**/api/repositories/repo-one/ask',async route=>{await waiting;await route.fulfill({json:{answer:'OLD_REPOSITORY_ANSWER',mode:'fixture',citations:[]}}).catch(()=>{})});
 await page.goto('/#/repositories/repo-one/repository');
 await page.getByLabel('Repository question').fill('How does this work?');await page.getByRole('button',{name:'Ask',exact:true}).click();
 await page.getByRole('button',{name:'local/repo-two',exact:true}).click();release();
 await expect(page.getByText('OLD_REPOSITORY_ANSWER')).toHaveCount(0);
});

test('operation progress is visible and failures can be retried',async({page})=>{
 await fixture(page);let retried=false;
 const operation={id:'operation-one',operation_id:'operation-one',kind:'refresh',subject:'repo-one',status:'failed',checkpoint:'acquisition',error:'Transport interrupted'};
 await page.route('**/api/repositories/repo-one/refresh',route=>route.fulfill({status:202,json:operation}));
 await page.route('**/api/operations/operation-one',route=>route.fulfill({json:operation}));
 await page.route('**/api/operations/operation-one/retry',async route=>{retried=true;await route.fulfill({json:{...operation,status:'complete',result:{id:'repo-one'}}})});
 await page.goto('/#/repositories/repo-one/repository');await page.getByRole('button',{name:'Refresh index'}).click();
 await expect(page.getByText('Transport interrupted',{exact:true})).toBeVisible();await page.getByRole('button',{name:'Retry operation'}).click();
 await expect.poll(()=>retried).toBe(true);
});
