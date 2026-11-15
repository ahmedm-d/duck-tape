import {test,expect} from '@playwright/test';
import {fixture,jobs} from './fixtures';

test('workspace sidebar expands repositories and opens all jobs across repositories',async({page})=>{
  await fixture(page);
  await page.route('**/api/jobs?summary=true',route=>route.fulfill({json:[...jobs,{...jobs[0],id:'other-job',repo_id:'repo-two',title:'Investigate slow builds',status:'Investigating'}]}));
  await page.goto('/dashboard');
  const nav=page.getByRole('navigation',{name:'Workspace'});
  await expect(nav.getByRole('button',{name:'Dashboard',exact:true})).toHaveAttribute('aria-current','page');
  await expect(nav.getByRole('button',{name:'Projects',exact:true})).toContainText('2');
  const repositories=nav.getByRole('button',{name:'Toggle projects',exact:true});
  await expect(repositories).toHaveAttribute('aria-expanded','false');
  await repositories.click();
  await expect(repositories).toHaveAttribute('aria-expanded','true');
  await nav.getByRole('button',{name:'local/repo-two',exact:true}).click();
  await expect(page).toHaveURL(/\/local\/repo-two\/overview$/);
  await page.getByRole('button',{name:'Back to workspace',exact:true}).click();
  await nav.getByRole('button',{name:'All Jobs',exact:true}).click();
  await expect(page).toHaveURL(/\/jobs$/);
  await expect(page.getByRole('heading',{name:'All Jobs',exact:true})).toBeVisible();
  await expect(page.locator('.job-row')).toHaveCount(3);
  await expect(nav.getByRole('button',{name:'All Jobs',exact:true})).toContainText('3');
  await page.reload();await expect(page.locator('.job-row')).toHaveCount(3);
  await page.locator('.job-row').filter({hasText:'Fix quoted prices'}).click();
  await expect(page).toHaveURL(/\/local\/repo-one\/jobs\/job-one$/);
  await page.goBack();await expect(page.getByRole('heading',{name:'All Jobs',exact:true})).toBeVisible();
});

test('investigations show their real count and remain accessible on mobile',async({page})=>{
  await fixture(page);
  await page.route('**/api/jobs?summary=true',route=>route.fulfill({json:[...jobs,{...jobs[0],id:'investigation',repo_id:'repo-two',title:'Investigate slow builds',status:'Investigating'}]}));
  await page.setViewportSize({width:390,height:844});
  await page.goto('/dashboard');
  const nav=page.getByRole('navigation',{name:'Workspace'});
  const investigations=nav.getByRole('button',{name:'All Investigations',exact:true});
  await expect(investigations).toContainText('1');
  await investigations.click();
  await expect(page).toHaveURL(/\/investigations$/);
  await expect(page.locator('.job-row')).toHaveCount(1);
  await expect(page.locator('.job-row')).toContainText('Investigate slow builds');
  await page.reload();await expect(page.locator('.job-row')).toHaveCount(1);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  await nav.getByRole('button',{name:'Toggle projects',exact:true}).click();
  await expect(nav.getByRole('button',{name:'local/repo-two',exact:true})).toBeVisible();
  await nav.getByRole('button',{name:'local/repo-two',exact:true}).click();
  await expect(page).toHaveURL(/\/local\/repo-two\/overview$/);
});


test('project sidebar scopes queues, supports review shortcuts and reloads project views',async({page})=>{
  await fixture(page);
  await page.route('**/api/jobs?summary=true',route=>route.fulfill({json:[...jobs,
    {...jobs[0],id:'investigation',title:'Investigate quote rounding',status:'Investigating'},
    {...jobs[0],id:'research',title:'Research faster parsing',status:'Proposed',source:'research'},
    {...jobs[0],id:'other',repo_id:'repo-two',title:'Other project review'}]}));
  await page.route('**/api/activity',route=>route.fulfill({json:[{id:'one',repo_id:'repo-one',message:'Project one indexed'},{id:'two',repo_id:'repo-two',message:'Project two indexed'}]}));
  await page.goto('/local/repo-one');
  const nav=page.getByRole('navigation',{name:'Project'});
  await expect(nav.getByLabel('Switch project')).toHaveValue('repo-one');
  await expect(page.getByRole('navigation',{name:'Workspace'})).toHaveCount(0);
  await expect(nav.locator('button[aria-expanded]')).toHaveCount(0);
  await nav.getByRole('button',{name:'Project jobs',exact:true}).click();
  await expect(page.locator('.job-row')).toHaveCount(4);
  await nav.getByRole('button',{name:'Project investigations',exact:true}).click();
  await expect(page).toHaveURL(/\/local\/repo-one\/investigations$/);
  await expect(page.locator('.job-row')).toHaveCount(1);
  await expect(page.locator('.job-row')).toContainText('Investigate quote rounding');
  await page.reload();await expect(page.locator('.job-row')).toHaveCount(1);
  await page.getByRole('button',{name:'Research proposals',exact:true}).click();
  await expect(page.locator('.job-row')).toContainText('Research faster parsing');
  await nav.getByRole('button',{name:'Project reviews',exact:true}).click();
  await expect(page.locator('.job-row')).toHaveCount(1);
  await page.locator('.job-row').click();
  await page.getByRole('button',{name:'Checks',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Verification evidence',exact:true})).toBeVisible();
  await nav.getByRole('button',{name:'Project knowledge',exact:true}).click();
  await expect(page).toHaveURL(/\/local\/repo-one\/knowledge$/);
  await page.reload();await expect(page.getByRole('heading',{name:'Knowledge',exact:true})).toBeVisible();
  await nav.getByRole('button',{name:'Project activity',exact:true}).click();
  await expect(page.locator('.activity-timeline')).toContainText('Project one indexed');
  await expect(page.locator('.activity-timeline')).not.toContainText('Project two indexed');
  await page.reload();await expect(page).toHaveURL(/\/local\/repo-one\/activity$/);
  await nav.getByLabel('Switch project').selectOption('repo-two');
  await nav.getByRole('button',{name:'Project reviews',exact:true}).click();
  await expect(page.locator('.job-row')).toContainText('Other project review');
  await page.setViewportSize({width:390,height:844});
  await nav.getByRole('button',{name:'Project investigations',exact:true}).click();
  await expect(page).toHaveURL(/\/local\/repo-two\/investigations$/);
  await nav.getByRole('button',{name:'Project jobs',exact:true}).click();
  await expect(page.locator('.job-row')).toHaveCount(1);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
});
