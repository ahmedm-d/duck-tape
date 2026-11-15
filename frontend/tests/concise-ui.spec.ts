import {test,expect} from '@playwright/test';
import {fixture} from './fixtures';

test('knowledge has no blank entry action and preserves supplied decisions',async({page})=>{
  await fixture(page);await page.goto('/local/repo-one/knowledge');
  await expect(page.getByRole('button',{name:/New entry|Add entry/})).toHaveCount(0);
  await expect(page.getByRole('region',{name:'Codebase knowledge map'})).toBeVisible();
  await page.getByRole('button',{name:'Decisions',exact:true}).click();
  await expect(page.getByRole('button',{name:/New entry|Add entry/})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Approve version 2'})).toBeVisible();
  await page.getByRole('button',{name:'Edit',exact:true}).click();
  await expect(page.getByRole('dialog',{name:'Edit knowledge'})).toBeVisible();
  await expect(page.getByLabel('Knowledge or decision')).toHaveValue('Keep integer cents');
});

test('settings sections focus controls and preserve unsaved provider fields',async({page})=>{
  await fixture(page);await page.goto('/local/repo-one/settings');
  const sections=page.getByRole('navigation',{name:'Settings sections'});
  await expect(page.getByRole('heading',{name:'Inference providers'})).toBeVisible();
  await expect(page.getByRole('heading',{name:'Continuous maintenance'})).toHaveCount(0);
  await page.getByLabel('Model name',{exact:true}).fill('unsaved-model');
  await sections.getByRole('button',{name:'Automation',exact:true}).click();
  await expect(page.getByLabel('Automation project')).toBeVisible();
  await expect(page.getByRole('button',{name:'Run now',exact:true})).toBeVisible();
  await expect(page.getByLabel('Provider endpoint')).toBeHidden();
  await sections.getByRole('button',{name:'Execution',exact:true}).click();
  await expect(page.getByText('Docker unavailable',{exact:true})).toBeVisible();
  await sections.getByRole('button',{name:'General',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Appearance',exact:true})).toBeVisible();
  await sections.getByRole('button',{name:'Providers',exact:true}).click();
  await expect(page.getByLabel('Model name',{exact:true})).toHaveValue('unsaved-model');
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
});

test('review navigation has one set of tabs and one page heading',async({page})=>{
  await fixture(page);await page.goto('/local/repo-one/jobs/job-one');
  await expect(page.getByRole('heading',{level:1})).toHaveCount(1);
  await expect(page.getByRole('button',{name:/^Changes\s*1$/})).toHaveCount(1);
  await expect(page.getByRole('navigation',{name:'Project'}).getByRole('button',{name:/Review /})).toHaveCount(0);
  await expect(page.getByText('Future capability',{exact:false})).toHaveCount(0);
  await page.getByRole('button',{name:'Checks',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Verification evidence'})).toBeVisible();
  await expect(page.getByText('AssertionError: expected integer cents',{exact:true})).toBeHidden();
  await page.locator('.check').filter({hasText:'candidate'}).locator('summary').click();
  await expect(page.getByText('AssertionError: expected integer cents',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Project reviews',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Reviews',exact:true})).toBeVisible();
  await expect(page.getByRole('heading',{level:1})).toHaveCount(1);
});
