import {test,expect} from '@playwright/test';
import {fixture,jobs} from './fixtures';

test('applied history shows exact approved diff and opens its job without GitHub',async({page})=>{
  await fixture(page);
  const completed={...jobs[0],status:'Completed',approval:{revision_id:'applied-revision'}};
  await page.route('**/api/jobs?summary=true',route=>route.fulfill({json:[completed,jobs[1],{...completed,id:'other',repo_id:'repo-two',title:'Other project change'}]}));
  await page.route('**/api/jobs/job-one',route=>route.fulfill({json:{...completed,managed_target_branch:'steward/integration',checks:[],revisions:[
    {id:'applied-revision',number:1,explanation:'Applied quantity fix',files:[{path:'pricing.py',before:'total = 1',after:'total = 2'}]},
    {id:'unapproved',number:2,explanation:'Do not show this revision',files:[]}
  ]}}));
  await page.goto('/local/repo-one/reviews');
  await page.getByRole('button',{name:'Applied changes',exact:true}).click();
  await expect(page.getByText('Applied quantity fix',{exact:true})).toBeVisible();
  await expect(page.getByText('Do not show this revision')).toHaveCount(0);
  await expect(page.getByRole('combobox',{name:'Applied change',exact:true}).locator('option')).toHaveCount(1);
  await expect(page.getByRole('link',{name:'Export applied patch'})).toHaveAttribute('href',/revision_id=applied-revision/);
  await page.getByRole('button',{name:'Open job and checks'}).click();
  await expect(page).toHaveURL(/\/jobs\/job-one$/);
});

test('ready for review is not reported as applied',async({page})=>{
  await fixture(page);await page.goto('/local/repo-one/reviews');
  await page.getByRole('button',{name:'Applied changes',exact:true}).click();
  await expect(page.getByRole('heading',{name:'No applied changes yet'})).toBeVisible();
});

test('applied history rejects detail belonging to another repository',async({page})=>{
  await fixture(page);
  await page.route('**/api/jobs?summary=true',route=>route.fulfill({json:[{...jobs[0],status:'Completed'}]}));
  await page.route('**/api/jobs/job-one',route=>route.fulfill({json:{...jobs[0],repo_id:'repo-two',status:'Completed'}}));
  await page.goto('/local/repo-one/reviews');
  await page.getByRole('button',{name:'Applied changes',exact:true}).click();
  await expect(page.getByRole('alert')).toContainText('no longer available');
  await expect(page.getByRole('button',{name:'Retry loading change'})).toBeVisible();
});
