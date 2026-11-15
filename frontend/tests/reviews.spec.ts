import {test,expect,Page} from '@playwright/test';
import {fixture,repositories} from './fixtures';
import {githubRepository} from '../src/github';
const sha='a'.repeat(40),nextSha='b'.repeat(40);
const pull=(commit=sha)=>({number:42,title:'Fix checkout retries',user:{login:'maya'},head:{ref:'fix/retry',sha:commit},base:{ref:'main'},draft:false,updated_at:'2026-11-14T09:00:00Z'});
async function githubFixture(page:Page){
  await fixture(page);
  await page.route('**/api/repositories?summary=true',route=>route.fulfill({json:[{...repositories[0],origin:{source:'https://github.com/acme/service.git'}},repositories[1]]}));
}

test('only canonical GitHub repository URLs are accepted',()=>{
  expect(githubRepository('https://github.com/acme/service.git')).toBe('acme/service');
  for(const input of ['https://github.com.evil.test/a/b','http://github.com/a/b','https://secret@github.com/a/b','https://github.com/a/b?token=x','https://github.com/a/b/pull/42','javascript:alert(1)','https://github.com/a/.'])expect(githubRepository(input)).toBeNull();
});

test('demo preview is labelled, navigable and never writes or fetches live project data',async({page})=>{
  const calls:string[]=[];
  page.on('request',request=>{if(request.url().includes('/api/')||request.url().includes('api.github.com'))calls.push(request.url())});
  await page.goto('/northstar/commerce-api/reviews?demo=1');
  await expect(page.locator('.preview-notice')).toContainText('Fictional data');
  await expect(page.locator('.pr-row')).toHaveCount(4);
  await expect(page.locator('.pr-row').first()).toContainText('fix/checkout-idempotency');
  await expect(page.locator('.ci-badge')).toHaveText(['Passed','Running','Failed','No checks']);
  await page.getByRole('button',{name:'Project knowledge',exact:true}).click();
  await expect(page).toHaveURL(/knowledge\?demo=1$/);await page.reload();
  await expect(page.locator('.graph-node')).toHaveCount(14);
  await page.getByRole('button',{name:'Project jobs',exact:true}).click();
  await page.getByRole('button',{name:'New request',exact:true}).click();
  await page.getByLabel('Title',{exact:true}).fill('Preview request');
  await page.getByLabel('Original signal & acceptance criteria').fill('Must not write to the backend');
  await page.getByRole('button',{name:'Create request',exact:true}).click();
  await expect(page.getByRole('dialog').getByRole('alert')).toContainText('Preview is read-only');
  expect(calls).toEqual([]);
});

test('PR branches, commit links and refreshed CI stay bound to the exact head SHA',async({page})=>{
  await githubFixture(page);let current=sha;const requests:string[]=[];
  await page.route('https://api.github.com/**',async route=>{
    const url=new URL(route.request().url());requests.push(url.pathname);
    const commit=url.pathname.split('/commits/')[1]?.split('/')[0];
    await route.fulfill({json:url.pathname.endsWith('/pulls')?[pull(current)]:url.pathname.endsWith('/check-runs')?{total_count:1,check_runs:[{head_sha:commit,status:'completed',conclusion:commit===sha?'success':'failure'}]}:{sha:commit,total_count:1,statuses:[{state:'success'}]}});
  });
  await page.goto('/local/repo-one/reviews');
  const row=page.getByRole('article',{name:'Pull request 42'});
  await expect(row).toContainText('fix/retry');await expect(row).toContainText('main');await expect(row.locator('.ci-badge')).toHaveText('Passed');
  await expect(row.getByRole('link',{name:sha.slice(0,7),exact:true})).toHaveAttribute('href','https://github.com/acme/service/commit/'+sha);
  expect(requests).toContain('/repos/acme/service/commits/'+sha+'/check-runs');
  expect(requests).toContain('/repos/acme/service/commits/'+sha+'/status');
  current=nextSha;await page.getByRole('button',{name:'Refresh',exact:true}).click();
  await expect(row.locator('.ci-badge')).toHaveText('Failed');
  await expect(row.getByRole('link',{name:nextSha.slice(0,7),exact:true})).toBeVisible();
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
});

for(const scenario of ['none','pending','skipped','partial','wrong-sha','forbidden'] as const)test('CI reports '+scenario+' without inventing success',async({page})=>{
  await githubFixture(page);
  await page.route('https://api.github.com/**',async route=>{
    const url=new URL(route.request().url());
    if(url.pathname.endsWith('/pulls'))return route.fulfill({json:[pull()]});
    if(scenario==='forbidden')return route.fulfill({status:403,json:{message:'API rate limit exceeded'}});
    const runs=scenario==='none'?[]:[{head_sha:scenario==='wrong-sha'?nextSha:sha,status:scenario==='pending'?'in_progress':'completed',conclusion:scenario==='skipped'?'skipped':'success'}];
    await route.fulfill({json:url.pathname.endsWith('/check-runs')?{total_count:scenario==='partial'?101:runs.length,check_runs:runs}:{sha,total_count:0,statuses:[]}});
  });
  await page.goto('/local/repo-one/reviews');
  await expect(page.locator('.ci-badge')).toHaveText(({none:'No checks',pending:'Running',skipped:'Skipped',partial:'Partial results','wrong-sha':'Unavailable',forbidden:'Unavailable'})[scenario]);
  if(scenario==='forbidden')await expect(page.getByRole('button',{name:'Retry CI '+sha.slice(0,7)})).toBeVisible();
});

test('public source selection persists and list errors do not fall back to demo rows',async({page})=>{
  await fixture(page);let limited=true;
  await page.route('https://api.github.com/**',route=>limited?route.fulfill({status:403,json:{message:'rate limit'}}):route.fulfill({json:[]}));
  await page.goto('/local/repo-one/reviews');await page.getByRole('button',{name:'Pull requests',exact:true}).click();
  await page.getByLabel('GitHub project').fill('https://github.com/acme/service');
  await page.getByRole('button',{name:'Load pull requests'}).click();
  await expect(page.getByRole('alert')).toContainText('limited or forbidden');await expect(page.locator('.pr-row')).toHaveCount(0);
  limited=false;await page.getByRole('button',{name:'Retry',exact:true}).click();
  await expect(page.getByRole('heading',{name:'No open pull requests'})).toBeVisible();
  await page.reload();await expect(page.locator('.pr-source')).toContainText('acme/service');
});

test('PR pagination uses the next page and clears the previous results',async({page})=>{
  await githubFixture(page);
  await page.route('https://api.github.com/**',route=>{
    const url=new URL(route.request().url());
    if(url.pathname.endsWith('/pulls'))return route.fulfill({headers:url.searchParams.get('page')==='1'?{'Access-Control-Expose-Headers':'Link',Link:'<https://api.github.com/repos/acme/service/pulls?page=2>; rel="next"'}:{},json:url.searchParams.get('page')==='1'?[pull()]:[]});
    return route.fulfill({json:url.pathname.endsWith('/check-runs')?{total_count:0,check_runs:[]}:{sha,total_count:0,statuses:[]}});
  });
  await page.goto('/local/repo-one/reviews');await expect(page.locator('.pr-row')).toHaveCount(1);
  await page.getByRole('button',{name:'Next',exact:true}).click();
  await expect(page.getByRole('heading',{name:'No open pull requests'})).toBeVisible();
  await expect(page.locator('.pr-row')).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Next',exact:true})).toBeDisabled();
});

test('switching projects aborts pending PR data from the previous source',async({page})=>{
  await githubFixture(page);let release:()=>void=()=>{};const pending=new Promise<void>(resolve=>release=resolve);
  await page.route('https://api.github.com/**',async route=>{await pending;await route.fulfill({json:[pull()]}).catch(()=>{})});
  await page.goto('/local/repo-one/reviews');await expect(page.getByRole('status')).toContainText('Loading pull requests');
  await page.getByLabel('Switch project').selectOption('repo-two');
  await page.getByRole('button',{name:'Project reviews',exact:true}).click();release();
  await page.getByRole('button',{name:'Pull requests',exact:true}).click();
  await expect(page.getByLabel('GitHub project')).toBeVisible();
  await expect(page.locator('.pr-row')).toHaveCount(0);
  await expect(page.locator('.pr-source')).toHaveCount(0);
});
