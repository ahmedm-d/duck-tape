import {test,expect} from '@playwright/test';
import {fixture} from './fixtures';

const jobs=[
  {id:'old',repo_id:'repo-one',title:'Handle retry storms',description:'Cap retries and add jitter to prevent duplicate charges.',status:'Ready for review',priority:'high',source:'ticket',type:'fix',risk:'medium',created_at:'2026-11-12T09:00:00Z'},
  {id:'new',repo_id:'repo-one',title:'Document webhooks',description:'Describe delivery guarantees.',status:'Proposed',priority:'low',source:'request',type:'feature',created_at:'2026-11-14T11:00:00Z'},
  {id:'middle',repo_id:'repo-two',title:'Reduce database reads',status:'Investigating',priority:'medium',source:'analysis',type:'performance',created_at:'2026-11-13T10:00:00Z'},
  {id:'unknown',repo_id:'repo-one',title:'Imported task',status:'Proposed',priority:'medium',source:'request',type:'fix'},
];
test.beforeEach(async({page})=>{
  await page.clock.setFixedTime(new Date('2026-11-14T12:00:00Z'));
  await fixture(page);
  await page.route('**/api/jobs?summary=true',route=>route.fulfill({json:jobs}));
});

test('dashboard charts use job data and request creation belongs to a project',async({page})=>{
  await page.goto('/dashboard');
  await expect(page.getByRole('button',{name:'New request',exact:true})).toHaveCount(0);
  const status=page.getByRole('region',{name:'Jobs by status'});
  await expect(status).toContainText('4 jobs');
  await expect(status.getByRole('button',{name:'Ready for review: 1'})).toBeVisible();
  const intake=page.getByRole('region',{name:'New jobs · last 7 days'});
  await expect(intake).toContainText('3 received');
  await expect(intake).toContainText('1 job without an arrival time');
  await status.getByRole('button',{name:'Ready for review: 1'}).click();
  await expect(page.getByLabel('Filter status')).toHaveValue('Ready for review');
  await expect(page.locator('.job-row')).toHaveCount(1);
  await page.goto('/local/repo-one');
  await page.getByRole('button',{name:'New request',exact:true}).click();
  await expect(page.getByRole('dialog').getByLabel('Project',{exact:true})).toHaveValue('repo-one');
  await expect(page.getByRole('dialog').getByLabel('Project',{exact:true})).toBeDisabled();
  const posted:Record<string,unknown>[]=[];
  await page.route('**/api/jobs',route=>{posted.push(route.request().postDataJSON());return route.fulfill({status:422,json:{detail:'Fixture: request inspected'}})});
  await page.getByLabel('Title',{exact:true}).fill('Scoped request');
  await page.getByLabel('Original signal & acceptance criteria').fill('Keep the selected project');
  await page.getByRole('button',{name:'Create request',exact:true}).click();
  await expect.poll(()=>posted.length).toBe(1);
  expect(posted[0].repo_id).toBe('repo-one');
});

test('dashboard handles empty projects and mobile charts without inventing activity',async({page})=>{
  await page.route('**/api/jobs?summary=true',route=>route.fulfill({json:[]}));
  await page.setViewportSize({width:390,height:844});
  await page.goto('/dashboard');
  await expect(page.getByRole('region',{name:'Jobs by status'})).toContainText('No jobs in this period');
  await expect(page.getByRole('region',{name:'New jobs · last 7 days'})).toContainText('0 received');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
});

test('jobs offer detailed tiles, list view, stable sorting, priority colours and arrival times',async({page})=>{
  await page.goto('/jobs');
  await page.getByRole('button',{name:'Tile view',exact:true}).click();
  await expect(page.locator('.job-list')).toHaveClass(/job-tiles/);
  await expect(page.locator('.job-row').first()).toContainText('Document webhooks');
  const old=page.locator('.job-row').filter({hasText:'Handle retry storms'});
  await expect(old).toContainText('Cap retries and add jitter');
  await expect(old).toContainText('Source: Ticket');
  await expect(old).toContainText('Type: Fix');
  await expect(old).toContainText('Risk: Medium');
  await expect(old.locator('time')).toHaveAttribute('datetime','2026-11-12T09:00:00Z');
  await expect(old.locator('.priority-high')).toContainText('High');
  const colours=await page.locator('.priority-high,.priority-medium,.priority-low').evaluateAll(nodes=>nodes.map(node=>getComputedStyle(node).color));
  expect(new Set(colours).size).toBe(3);
  await page.getByLabel('Sort jobs').selectOption('priority');
  await expect(page.locator('.job-row').first()).toContainText('Handle retry storms');
  await page.getByLabel('Sort jobs').selectOption('oldest');
  await expect(page.locator('.job-row').first()).toContainText('Handle retry storms');
  await expect(page.locator('.job-row').last()).toContainText('Arrival time unavailable');
  await page.getByRole('button',{name:'List view',exact:true}).click();
  await expect(page.locator('.job-list')).not.toHaveClass(/job-tiles/);
  await page.reload();await expect(page.getByRole('button',{name:'List view',exact:true})).toHaveAttribute('aria-pressed','true');
  await page.getByLabel('Sort jobs').selectOption('status');
  await expect(page.locator('.job-row').first()).toContainText('Reduce database reads');
  await page.getByLabel('Search jobs').fill('duplicate charges');
  await expect(page.locator('.job-row')).toHaveCount(1);
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
});
