import {test,expect} from '@playwright/test';
import {fixture} from './fixtures';

test('deep routes survive reload and browser back',async({page})=>{
 await fixture(page);await page.goto('/#/repositories/repo-one/knowledge');
 await expect(page.getByRole('heading',{name:'Knowledge',exact:true})).toBeVisible();
 await page.reload();await expect(page.getByRole('heading',{name:'Knowledge',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Jobs',exact:true}).click();
 await expect(page).toHaveURL(/repo-one\/jobs$/);
 await page.goBack();await expect(page.getByRole('heading',{name:'Knowledge',exact:true})).toBeVisible();
});

test('job filters are independent from source search',async({page})=>{
 await fixture(page);await page.goto('/#/repositories/repo-one/jobs');
 await page.getByLabel('Filter source').selectOption('ticket');
 await page.getByLabel('Filter type').selectOption('fix');
 await page.getByLabel('Filter priority').selectOption('high');
 await page.getByLabel('Filter risk').selectOption('low');
 await page.getByLabel('Search jobs').fill('quoted');
 await expect(page.getByRole('button').filter({hasText:'Fix quoted prices'})).toBeVisible();
 await expect(page.getByRole('button').filter({hasText:'Dependency advice'})).toHaveCount(0);
 await page.getByRole('button',{name:'Repository',exact:true}).click();
 await expect(page.getByLabel('Search source')).toHaveValue('');
});

test('polling does not repeatedly fetch the full source snapshot',async({page})=>{
 const calls=await fixture(page);await page.goto('/#/repositories/repo-one/repository');
 await expect(page.getByRole('heading',{name:'Repository',exact:true})).toBeVisible();
 await expect(page.getByText('Indexed project',{exact:true})).toBeVisible();
 const initial=calls.filter(path=>path==='/repositories/repo-one').length;
 await page.waitForTimeout(3400);
 expect(calls.filter(path=>path==='/repositories/repo-one').length).toBe(initial);
});

test('narrow dark layout supports keyboard navigation without overflow',async({page})=>{
 await fixture(page);await page.setViewportSize({width:390,height:844});await page.goto('/#/repositories/repo-one/jobs');
 await page.getByRole('button',{name:'Toggle theme'}).click();
 await expect(page.locator('html')).toHaveAttribute('data-theme','dark');
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
 await page.keyboard.press('Tab');expect(await page.evaluate(()=>document.activeElement?.tagName)).not.toBe('BODY');
});
