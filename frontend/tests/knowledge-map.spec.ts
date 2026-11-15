import {test,expect,Page} from '@playwright/test';
import {fixture,repositories} from './fixtures';

async function mapFixture(page:Page,count=4) {
  await fixture(page);
  const paths=['src/api.ts','src/store.ts','tests/api.test.ts','docs/guide.md',...Array.from({length:Math.max(0,count-4)},(_,i)=>`extra/file-${i}.ts`)].slice(0,count);
  await page.route('**/api/repositories/repo-one',route=>route.fulfill({json:{...repositories[0],index:{files:paths.map(path=>({path,status:'indexed',content:'export const source = true;'})),edges:count>1?[{source:paths[0],target:paths[1],kind:'imports',confidence:'parsed/resolved',line:1},{source:paths[2],target:paths[0],kind:'tests',confidence:'parsed/resolved'},{source:paths[0],target:'external.ts',kind:'imports'}]:[],gaps:['Dynamic calls are incomplete']}}}));
}

test('knowledge map explores real relationships and opens source',async({page})=>{
  await mapFixture(page);await page.goto('/local/repo-one/knowledge');
  await expect(page.getByRole('heading',{level:1})).toHaveCount(1);
  await expect(page.locator('.graph-node')).toHaveCount(4);
  await expect(page.locator('.graph-edge')).toHaveCount(2);
  await page.getByRole('button',{name:'Inspect src/api.ts',exact:true}).focus();await page.keyboard.press('Enter');
  const inspector=page.getByRole('complementary',{name:'Graph file details'});
  await expect(inspector).toContainText('2 relationships');
  await expect(inspector).toContainText('parsed/resolved');
  await page.getByLabel('Graph relationship').selectOption('imports');
  await expect(page.locator('.graph-edge')).toHaveCount(1);
  await page.getByLabel('Find graph file').fill('api.ts');
  await expect(page.locator('.graph-node')).toHaveCount(2);
  await page.getByRole('button',{name:'Zoom in',exact:true}).click();
  await expect(page.locator('.graph-controls')).toContainText('125%');
  await page.getByRole('button',{name:'Reset graph view'}).click();
  await expect(page.locator('.graph-controls')).toContainText('100%');
  await page.getByRole('button',{name:'Open source',exact:true}).click();
  await expect(page).toHaveURL(/\/codebase\?tab=files$/);
  await expect(page.locator('.source-code')).toContainText('export const source = true');
  await page.getByRole('button',{name:'Project knowledge',exact:true}).click();
  await page.getByRole('button',{name:'Decisions',exact:true}).click();
  await expect(page.getByRole('button',{name:'Approve version 2'})).toBeVisible();
});

test('empty and bounded maps stay honest and searchable',async({page})=>{
  await mapFixture(page,260);await page.goto('/local/repo-one/knowledge');
  await expect(page.locator('.graph-node')).toHaveCount(250);
  await expect(page.getByRole('status')).toContainText('Showing 250');
  await page.getByLabel('Find graph file').fill('tests/api.test.ts');
  await expect(page.getByRole('button',{name:'Inspect tests/api.test.ts',exact:true})).toBeVisible();
  await page.getByLabel('Find graph file').fill('missing');
  await expect(page.getByText('No matching files',{exact:true})).toBeVisible();
  await mapFixture(page,0);await page.reload();
  await expect(page.getByText('No indexed files yet',{exact:true})).toBeVisible();
});

test('dark palettes persist and map fits a narrow screen',async({page})=>{
  await mapFixture(page);await page.goto('/local/repo-one/knowledge');
  const accents=new Set<string>();
  for(const theme of ['dark','forest','midnight','violet']) {
    await page.getByLabel('Colour theme').selectOption(theme);
    await expect(page.locator('html')).toHaveAttribute('data-theme',theme);
    accents.add(await page.locator('html').evaluate(node=>getComputedStyle(node).getPropertyValue('--accent')));
    await expect(page.locator('html')).toHaveCSS('background-color','rgb(13, 17, 23)');
    await expect(page.locator('.knowledge-map')).toHaveCSS('background-color','rgb(22, 27, 34)');
    await expect(page.locator('.sidebar')).toHaveCSS('background-color',await page.locator('html').evaluate(node=>getComputedStyle(node).backgroundColor));
  }
  expect(accents.size).toBe(4);
  await page.reload();await expect(page.locator('html')).toHaveAttribute('data-theme','violet');
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('button',{name:'Inspect src/api.ts',exact:true}).click();
  await expect(page.getByRole('button',{name:'Open source',exact:true})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
});
