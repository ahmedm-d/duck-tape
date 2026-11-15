import {test,expect} from '@playwright/test';
import {fixture} from './fixtures';

test('codebase tabs and activity remain reachable through reload and back', async ({page}) => {
 await fixture(page); await page.goto('/local/repo-one/codebase');
 await page.getByRole('button',{name:'Project knowledge',exact:true}).click();
 await expect(page).toHaveURL(/\/knowledge$/); await page.reload();
 await expect(page.getByRole('heading',{name:'Knowledge',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Project codebase',exact:true}).click();
 await page.getByRole('button',{name:'Files',exact:true}).click();
 await expect(page).toHaveURL(/tab=files$/); await page.reload();
 await expect(page.getByRole('button',{name:'Files',exact:true})).toHaveClass('active');
 await page.goBack();await page.goBack();
 await expect(page.getByRole('heading',{name:'Knowledge',exact:true})).toBeVisible();
 await page.goto('/local/repo-one/codebase?tab=knowledge');await expect(page.getByRole('heading',{name:'Knowledge',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Activity',exact:true}).click();
 await expect(page).toHaveURL(/\/activity$/); await page.reload();
 await expect(page.getByRole('heading',{name:'Activity',exact:true})).toBeVisible();
});
test('landing leads to cross-repository dashboard without selecting a repository',async({page})=>{
 await fixture(page);await page.goto('/');await expect(page.getByRole('heading',{name:'Duck Tape holds your codebase together.'})).toBeVisible();await expect(page.locator('.sidebar')).toHaveCount(0);await page.getByRole('link',{name:'Start creating'}).click();await expect(page).toHaveURL(/\/dashboard$/);await expect(page.locator('.repository-card')).toHaveCount(2);await expect(page.getByRole('button',{name:'New request',exact:true})).toHaveCount(0);await page.locator('.repository-card').first().click();await page.getByRole('button',{name:'New request',exact:true}).click();await expect(page.locator('select[name=repo_id]')).toHaveValue('repo-one');
});
test('repository paths survive reload and back, job back always targets parent',async({page})=>{
 await fixture(page);await page.goto('/local/repo-one/jobs/job-one');await expect(page.getByRole('heading',{name:'Fix quoted prices'})).toBeVisible();await page.reload();await expect(page.getByRole('heading',{name:'Fix quoted prices'})).toBeVisible();await page.getByRole('button',{name:'Back to project'}).click();await expect(page).toHaveURL(/\/local\/repo-one$/);await page.getByRole('button',{name:'Project codebase',exact:true}).click();await expect(page).toHaveURL(/\/local\/repo-one\/codebase$/);await page.goBack();await expect(page).toHaveURL(/\/local\/repo-one$/);await page.goBack();await expect(page.getByRole('heading',{name:'Fix quoted prices'})).toBeVisible();
});
test('a job cannot render under another repository',async({page})=>{
 await fixture(page);await page.goto('/local/repo-two/jobs/job-one');await expect(page.getByRole('alert')).toContainText('does not belong');await expect(page.getByRole('heading',{name:'Fix quoted prices'})).toHaveCount(0);
});
test('decision is selected before confirmation posts and apply names exact branch',async({page})=>{
 await fixture(page);const posts:any[]=[];let selected=false;let approved=false;
 await page.route('**/api/jobs/job-decision**',async route=>{const request=route.request();if(request.method()==='POST'){posts.push({url:request.url(),body:request.postDataJSON()});selected=true;if(request.url().endsWith('/accept'))approved=true;await route.fulfill({json:{}});return}await route.fulfill({json:{id:'job-decision',repo_id:'repo-one',title:'Decision job',status:selected?'Ready for review':'Needs input',current_revision:'rev-choice',managed_target_branch:'steward/integration',approval:approved?{revision_id:'rev-choice'}:null,decisions:selected?[]:[{id:'choice',title:'Choose behavior',options:[{id:'reject',label:'Reject oversized orders',tradeoff:'Keep bounded requests'}]}],revisions:[{id:'rev-choice',number:1,files:[],explanation:'Bound requests'}]}})});
 await page.goto('/local/repo-one/jobs/job-decision');await expect(page.getByRole('button',{name:'Confirm choice'})).toBeDisabled();await page.getByRole('button',{name:/Reject oversized orders/}).click();expect(posts).toHaveLength(0);await page.getByRole('button',{name:'Confirm choice'}).click();await expect(page.getByRole('button',{name:'Approve & apply'})).toBeVisible();expect(posts[0].body).toEqual({decision_id:'choice',option:'reject'});await expect(page.getByText('Target branch:')).toContainText('steward/integration');await page.getByRole('button',{name:'Approve & apply'}).click();await expect.poll(()=>posts.length).toBe(3);expect(posts[1].body).toEqual({revision_id:'rev-choice'});expect(posts[2].body).toEqual({revision_id:'rev-choice',target_branch:'steward/integration'});
});
test('dashboard shows jobs from every repository and screen previews link to real pages',async({page})=>{
 await fixture(page);await page.route('**/api/jobs?summary=true',route=>route.fulfill({json:[{id:'other-job',repo_id:'repo-two',title:'Other repository request',status:'Awaiting approval',source:'request',type:'fix',priority:'high'}]}));await page.goto('/dashboard');await expect(page.locator('.job-row')).toContainText('local/repo-two');await expect(page.getByRole('button',{name:/Other repository request/})).toBeVisible();await page.goto('/screens');await expect(page.getByRole('heading',{name:'Workspace screens'})).toBeVisible();await expect(page.locator('iframe')).toHaveCount(5);await expect(page.getByRole('link',{name:'Open page'}).first()).toHaveAttribute('href','/');
});
test('workspace pages stay usable at narrow and wide sizes',async({page})=>{
 await fixture(page);const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
 for(const route of ['/','/dashboard','/local/repo-one','/local/repo-one/codebase','/settings']){
  await page.goto(route);await expect(page.locator('h1').first()).toBeVisible();
  for(const width of [1440,390]){await page.setViewportSize({width,height:950});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();await page.screenshot({path:'ux-evidence/navigation/'+(route.replaceAll('/','_')||'landing')+'-'+width+'.png',fullPage:true})}
 }
 expect(errors).toEqual([]);
});
test('slow repository discovery does not reject a valid job deep link',async({page})=>{
 await fixture(page);await page.route('**/api/repositories?summary=true',async route=>{await new Promise(resolve=>setTimeout(resolve,500));await route.fallback()});await page.goto('/local/repo-one/jobs/job-one');await expect(page.getByRole('heading',{name:'Fix quoted prices'})).toBeVisible();await expect(page.getByRole('alert')).toHaveCount(0);await page.goto('/local/repo-two/jobs/job-one');await expect(page.getByRole('alert')).toBeVisible();await page.getByLabel('Switch project').selectOption('repo-one');await expect(page.getByRole('alert')).toHaveCount(0);
});
test('new requests stay scoped to their project and fixture controls match it',async({page})=>{
 await fixture(page);await page.route('**/api/repositories?summary=true',route=>route.fulfill({json:[{id:'sample',name:'Sample',is_sample:true},{id:'imported',name:'Imported',is_sample:false}]}));
 await page.goto('/local/sample');await page.getByRole('button',{name:'New request',exact:true}).click();
 await expect(page.getByRole('dialog').getByLabel('Project',{exact:true})).toHaveValue('sample');await expect(page.getByRole('dialog').getByLabel('Project',{exact:true})).toBeDisabled();
 await expect(page.getByLabel('Offline fixture scenario')).toBeVisible();await page.getByLabel('Offline fixture scenario').selectOption('bug');
 await page.getByRole('button',{name:'Close dialog'}).click();await page.getByLabel('Switch project').selectOption('imported');
 await page.getByRole('button',{name:'New request',exact:true}).click();await expect(page.getByLabel('Offline fixture scenario')).toHaveCount(0);
 await expect(page.getByRole('dialog').getByLabel('Project',{exact:true})).toHaveValue('imported');
});
test('workspace and gallery avoid health probes while settings waits explicitly',async({page})=>{
 const calls=await fixture(page);let release:()=>void=()=>{};const waiting=new Promise<void>(resolve=>release=resolve);const settingsRequests:string[]=[];
 await page.route('**/api/settings',async route=>{settingsRequests.push(route.request().frame().url());await waiting;await route.fulfill({json:{providers:{kind:'ollama',model:'loaded-model',endpoint:'http://127.0.0.1:11434',available:true},sandbox:{available:true}}})});
 await page.goto('/dashboard');await expect(page.locator('.repository-card')).toHaveCount(2);expect(settingsRequests).toHaveLength(0);expect(calls).not.toContain('/settings');
 await page.goto('/screens');await expect(page.frameLocator('iframe[title="Dashboard preview"]').locator('.repository-card')).toHaveCount(2);await expect(page.frameLocator('iframe[title="Codebase preview"]').getByRole('heading',{name:'Architecture & capabilities'})).toBeVisible();await expect(page.frameLocator('iframe[title="Settings preview"]').getByRole('heading',{name:'Loading settings'})).toBeVisible();expect(settingsRequests.length).toBeGreaterThan(0);expect(settingsRequests.every(url=>new URL(url).pathname==='/settings')).toBeTruthy();
 release();await expect(page.frameLocator('iframe[title="Settings preview"]').getByLabel('Model name')).toHaveValue('loaded-model');
});
