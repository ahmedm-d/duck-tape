import {test,expect} from '@playwright/test';
import {fixture,jobs} from './fixtures';
test('verification separates phases and scopes baselines to the selected revision',async({page})=>{
 await fixture(page);
 await page.route('**/api/jobs/job-one',route=>route.fulfill({json:{...jobs[0],current_revision:'v2',
 revisions:[{id:'v1',number:1,base_snapshot:'b1',files:[]},{id:'v2',number:2,base_snapshot:'b2',files:[]}],
 runs:[{id:'r1',revision_id:'v1'},{id:'r2',revision_id:'v2'}],
 checks:[{id:'old',phase:'baseline',run_id:'r1',base_snapshot:'b1',status:'failed',command:'old baseline'},
 {id:'base',phase:'baseline',run_id:'r2',base_snapshot:'b2',status:'failed',command:'current baseline'},
 {id:'before',phase:'regression-before',revision_id:'v2',status:'failed',command:'reproduction'},
 {id:'after',phase:'candidate',revision_id:'v2',status:'passed',command:'candidate tests'}]}}));
 await page.goto('/local/repo-one/jobs/job-one');
 await page.getByRole('button',{name:/^Changes/}).click();
 await expect(page.getByText('Candidate checks: 1 of 1 passed.',{exact:false})).toBeVisible();
 await expect(page.getByText('Baseline checks: 0 of 1 passed.',{exact:false})).toBeVisible();
 await page.getByRole('button',{name:'Checks',exact:true}).click();
 await expect(page.getByText('current baseline',{exact:true})).toBeVisible();await expect(page.getByText('old baseline',{exact:true})).toHaveCount(0);
 await page.getByRole('button',{name:/^Changes/}).click();await page.getByLabel('Patch revision').selectOption('v1');
 await page.getByRole('button',{name:'Checks',exact:true}).click();
 await expect(page.getByText('old baseline',{exact:true})).toBeVisible();await expect(page.getByText('current baseline',{exact:true})).toHaveCount(0);
});
