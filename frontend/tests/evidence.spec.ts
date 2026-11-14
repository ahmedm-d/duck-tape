import {test,expect} from '@playwright/test';
import {fixture} from './fixtures';

test('unchanged job details are not downloaded during summary polling',async({page})=>{
 const calls=await fixture(page);await page.goto('/#/repositories/repo-one/jobs/job-one');
 await expect(page.getByRole('heading',{name:'Fix quoted prices'})).toBeVisible();
 const initial=calls.filter(path=>path==='/jobs/job-one').length;await page.waitForTimeout(3400);
 expect(calls.filter(path=>path==='/jobs/job-one').length).toBe(initial);
});

test('a response bound to a different snapshot shows a stale warning',async({page})=>{
 await fixture(page);await page.route('**/api/repositories/repo-one/ask',route=>route.fulfill({json:{snapshot:'old-snapshot',answer:'OUTDATED_ANSWER',citations:[]}}));
 await page.goto('/#/repositories/repo-one/repository');await page.getByLabel('Repository question').fill('How does this work?');await page.getByRole('button',{name:'Ask',exact:true}).click();
 await expect(page.getByText('The repository snapshot changed while this evidence was loading. Refresh the index before relying on this answer.')).toBeVisible();
 await expect(page.getByText('OUTDATED_ANSWER')).toHaveCount(0);
});

test('syntax highlighting is present in unified and split views',async({page})=>{
 await fixture(page);await page.goto('/#/repositories/repo-one/jobs/job-one');
 await expect(page.locator('.hljs-keyword').first()).toBeVisible();
 await expect(page.locator('[aria-label="Changed files"]')).toContainText('Modified');
 await page.getByRole('button',{name:'Split view'}).click();
 await expect(page.getByText('Before',{exact:true})).toBeVisible();await expect(page.getByText('After',{exact:true})).toBeVisible();
 await expect(page.locator('.split-diff .hljs-keyword').first()).toBeVisible();
});
