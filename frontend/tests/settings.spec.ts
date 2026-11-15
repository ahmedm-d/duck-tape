import {test,expect} from '@playwright/test';
import {fixture} from './fixtures';

test('provider form saves only nonsecret configuration and discloses environment overrides',async({page})=>{
 await fixture(page);let saved:any;
 await page.route('**/api/settings/provider',async route=>{saved=route.request().postDataJSON();await route.fulfill({json:{configuration:saved,saved:true,environment_overrides:['endpoint','model']}})});
 await page.goto('/#/repositories/repo-one/settings');
 await page.getByLabel('Provider endpoint').fill('http://127.0.0.1:11434');
 await page.getByLabel('Model name',{exact:true}).fill('qwen-coder');
 await page.getByLabel('Context limit').fill('8192');
 await expect(page.getByLabel('Embedding endpoint (optional)',{exact:true})).toHaveValue('');
 await page.getByLabel('Embedding endpoint (optional)',{exact:true}).fill('http://127.0.0.1:11437/v1');
 await page.getByRole('button',{name:'Save provider configuration'}).click();
 await expect.poll(()=>saved).toEqual({kind:'openai',endpoint:'http://127.0.0.1:11434',model:'qwen-coder',small_model:'',context_limit:8192,embedding_model:'',embedding_endpoint:'http://127.0.0.1:11437/v1'});
 await expect(page.getByText('Environment-managed fields: endpoint, model',{exact:true})).toBeVisible();
 await expect(page.locator('input[type="password"]')).toHaveCount(0);
});


test('hosted providers choose the right protocol without collecting secrets',async({page})=>{
 await fixture(page);let saved:any;
 await page.route('**/api/settings/provider',async route=>{saved=route.request().postDataJSON();await route.fulfill({json:{configuration:saved,saved:true,environment_overrides:[]}})});
 await page.goto('/#/repositories/repo-one/settings');
 await page.getByLabel('Provider protocol').selectOption('adaption');
 await expect(page.getByLabel('Provider endpoint')).toHaveValue('https://api.prod.adaptionlabs.ai/api/v1');
 await page.getByLabel('Model name',{exact:true}).fill('fixture-adaption-model');
 await page.getByRole('button',{name:'Save provider configuration'}).click();
 await expect.poll(()=>saved?.kind).toBe('adaption');
 await page.getByLabel('Provider protocol').selectOption('huggingface');
 await expect(page.getByLabel('Provider endpoint')).toHaveValue('https://router.huggingface.co/v1');
 await page.getByLabel('Provider protocol').selectOption('anthropic');
 await expect(page.getByLabel('Provider endpoint')).toHaveValue('https://api.anthropic.com');
 await expect(page.getByLabel('Model name',{exact:true})).toHaveValue('');
 await page.getByLabel('Model name',{exact:true}).fill('claude-opus-5-5');
 await page.getByRole('button',{name:'Save provider configuration'}).click();
 await expect.poll(()=>saved?.kind).toBe('anthropic');
 await expect(page.getByLabel('Model name',{exact:true})).toHaveValue('claude-opus-5-5');
 await expect(page.locator('input[type="password"]')).toHaveCount(0);
});
