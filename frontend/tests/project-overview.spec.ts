import {test,expect} from '@playwright/test';
import {fixture,jobs} from './fixtures';

test('recent projects are bounded and Projects opens the full searchable list',async({page})=>{
 await fixture(page);
 await page.route('**/api/repositories?summary=true',route=>route.fulfill({json:Array.from({length:7},(_,i)=>({id:'repo-'+i,name:'Project '+i,updated_at:`2026-11-${String(i+1).padStart(2,'0')}T10:00:00Z`}))}));
 await page.goto('/dashboard');await expect(page.getByRole('heading',{name:'Recent projects'})).toBeVisible();
 await expect(page.locator('.repository-card')).toHaveCount(5);
 await expect(page.locator('.repository-card').first()).toContainText('Project 6');
 await page.getByRole('navigation',{name:'Workspace'}).getByRole('button',{name:'Projects',exact:true}).click();
 await expect(page).toHaveURL(/\/projects$/);await expect(page.locator('.repository-card')).toHaveCount(7);
 await page.getByLabel('Search projects').fill('Project 0');await expect(page.locator('.repository-card')).toHaveCount(1);
 await page.locator('.repository-card').click();await expect(page).toHaveURL(/\/local\/repo-0\/overview$/);
});

test('project overview scopes metrics and timeline drill-down preserves its period',async({page})=>{
 await page.clock.setFixedTime(new Date('2026-11-14T12:00:00Z'));await fixture(page);
 await page.route('**/api/jobs?summary=true',route=>route.fulfill({json:[{...jobs[0],created_at:'2026-11-13T10:00:00Z'},{...jobs[0],id:'old',created_at:'2026-08-01T10:00:00Z'},{...jobs[0],id:'other',repo_id:'repo-two',status:'Failed',created_at:'2026-11-13T10:00:00Z'}]}));
 await page.goto('/local/repo-one/overview');await expect(page.getByRole('heading',{name:'Overview',exact:true})).toBeVisible();
 const chart=page.getByRole('region',{name:'Jobs by status'});await expect(chart).toContainText('2 jobs');
 await page.getByLabel('Status timeline').selectOption('7');await expect(chart).toContainText('1 job');
 await chart.getByRole('button',{name:'Ready for review: 1'}).click();
 await expect(page.locator('.job-row')).toHaveCount(1);await expect(page.getByLabel('Received within')).toHaveValue('7');
 await page.getByRole('button',{name:'Project overview',exact:true}).click();await page.reload();
 await expect(page.getByRole('heading',{name:'Overview',exact:true})).toBeVisible();
});

test('investigation links show recorded reasons, missing assessments and safe source evidence',async({page})=>{
 await fixture(page);
 await page.route('**/api/jobs/job-one',route=>route.fulfill({json:{...jobs[0],priority_reason:'Checkout retries can charge customers twice.',risk_reason:'Bounded to payment retries, with regression coverage.',source_url:'https://github.com/acme/service/issues/42',investigation:{summary:'A retry path lacks idempotency.',evidence:[{path:'pricing.py',start_line:1,end_line:2}],missing:['Production frequency is unknown']}}}));
 await page.goto('/local/repo-one');await page.locator('.job-card').first().getByRole('button',{name:'Investigation',exact:true}).click();
 await expect(page).toHaveURL(/\/jobs\/job-one\/investigation$/);
 await expect(page.getByText('Checkout retries can charge customers twice.')).toBeVisible();
 await expect(page.getByText('Bounded to payment retries, with regression coverage.')).toBeVisible();
 await expect(page.getByRole('link',{name:'Original source'})).toHaveAttribute('href','https://github.com/acme/service/issues/42');
 await expect(page.getByText('Production frequency is unknown')).toBeVisible();
 await page.reload();await expect(page.getByRole('heading',{name:'Investigation',exact:true})).toBeVisible();
 await page.goto('/local/repo-two/jobs/job-one/investigation');await expect(page.getByText('Checkout retries can charge customers twice.')).toHaveCount(0);
});

test('full file shows exact captured before and after content and closes with Escape',async({page})=>{
 await fixture(page);await page.goto('/local/repo-one/jobs/job-one');
 await page.getByRole('button',{name:'View full file',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'Full file: pricing.py'});
 await expect(dialog).toBeVisible();await expect(dialog.locator('code')).toContainText('return 20');
 await dialog.getByLabel('File version').selectOption('before');await expect(dialog.locator('code')).toContainText('return 10');
 await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0);await expect(page.getByRole('button',{name:'View full file',exact:true})).toBeFocused();
 await page.getByRole('button',{name:'Split view',exact:true}).click();await page.getByRole('button',{name:'View full file',exact:true}).click();await expect(dialog).toBeVisible();
});


test('full file handles additions, deletions, empty and binary files without substituting current code',async({page})=>{
 await fixture(page);
 await page.route('**/api/jobs/job-one',route=>route.fulfill({json:{...jobs[0],revisions:[{id:'rev-one',number:1,files:[{path:'new.ts',before:null,after:''},{path:'removed.ts',before:'captured old code',after:null},{path:'image.png',binary:true}],diff:''}]}}));
 await page.goto('/local/repo-one/jobs/job-one');
 await page.getByRole('button',{name:'View full file',exact:true}).click();
 let dialog=page.getByRole('dialog');await expect(dialog).toContainText('Empty file');
 await dialog.getByLabel('File version').selectOption('before');await expect(dialog).toContainText('This version has no captured file content.');await dialog.getByRole('button',{name:'Close file'}).click();
 await page.getByLabel('File to view').selectOption('removed.ts');await page.getByRole('button',{name:'View full file',exact:true}).click();
 await expect(dialog.locator('code')).toHaveText('captured old code');await expect(dialog.getByLabel('File version')).toHaveValue('before');await page.keyboard.press('Escape');
 await page.getByLabel('File to view').selectOption('image.png');await page.getByRole('button',{name:'View full file',exact:true}).click();await expect(dialog).toContainText('Binary file.');
});

test('investigation uses saved run evidence and exposes missing rationale',async({page})=>{
 await fixture(page);
 await page.route('**/api/jobs/job-one',route=>route.fulfill({json:{...jobs[0],source_url:'javascript:alert(1)',runs:[{base_snapshot:'older-snapshot',investigation:{summary:'Recorded run assessment',evidence:[{path:'pricing.py',start_line:1,end_line:2}]}}]}}));
 await page.goto('/local/repo-one/jobs/job-one/investigation');
 await expect(page.getByText('Recorded run assessment')).toBeVisible();await expect(page.getByText('No priority rationale recorded.')).toBeVisible();await expect(page.getByText('No risk rationale recorded.')).toBeVisible();await expect(page.getByRole('link',{name:'Original source'})).toHaveCount(0);
 await page.getByRole('button',{name:'pricing.py:1'}).click();await expect(page.getByRole('alert')).toContainText('snapshot changed');
});

test('timeline excludes older, future and undated jobs and its dropdown supports keyboard selection',async({page})=>{
 await page.clock.setFixedTime(new Date('2026-11-14T12:00:00Z'));await fixture(page);
 await page.route('**/api/jobs?summary=true',route=>route.fulfill({json:['2026-11-08T00:00:00Z','2026-11-07T23:59:59Z','2026-11-15T00:00:00Z',undefined].map((created_at,i)=>({...jobs[0],id:String(i),created_at}))}));
 await page.goto('/dashboard');const select=page.getByLabel('Status timeline');await select.focus();await page.keyboard.press('Space');await page.keyboard.press('ArrowDown');await page.keyboard.press('Enter');
 await expect(select).toHaveValue('7');await expect(page.getByRole('region',{name:'Jobs by status'})).toContainText('1 job');
});


test('project overview counts only successfully indexed source and test files',async({page})=>{
 await fixture(page);
 await page.route('**/api/repositories/repo-one',route=>route.fulfill({json:{id:'repo-one',name:'Coverage fixture',index:{files:[{path:'src/main.ts',status:'indexed'},{path:'tests/main.test.ts',status:'indexed'},{path:'tests/large.test.ts',status:'failed'},{path:'vendor/test.js',status:'excluded'},{path:'photo.png',status:'unsupported'}]}}}));
 await page.goto('/local/repo-one/overview');await expect(page.getByRole('button',{name:'2 Indexed files',exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'1 Test files',exact:true})).toBeVisible();
});
