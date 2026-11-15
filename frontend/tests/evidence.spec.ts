import {test,expect} from '@playwright/test';
import {fixture} from './fixtures';
import {alignedLines,changedLines} from '../src/components/DiffViewer';

test('split alignment pads added and deleted files without inventing trailing lines',()=>{
 const additions={before:new Set<number>(),after:new Set([1,2])};
 expect(alignedLines('', 'first\nsecond\n', additions).map(row=>[row.before,row.after?.number])).toEqual([[null,1],[null,2]]);
 const deletions={before:new Set([1,2]),after:new Set<number>()};
 expect(alignedLines('first\nsecond\n', null, deletions).map(row=>[row.before?.number,row.after])).toEqual([[1,null],[2,null]]);
 expect(alignedLines('','')).toEqual([]);
 expect(alignedLines('\n','\n')).toHaveLength(1);
});

test('split diff colours follow hunk positions across insertions, removals and renamed files',()=>{
 const changes=changedLines('--- a/old name.py\n+++ b/new name.py\n@@ -1,2 +1,3 @@\n same\n+inserted\n last\n@@ -20,2 +21,1 @@\n---removed text starting with dashes\n keep\n--- /dev/null\n+++ b/added.py\n@@ -0,0 +1 @@\n+new\n--- a/deleted.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n\\ No newline at end of file\n');
 expect([...changes.get('new name.py')!.before]).toEqual([20]);
 expect([...changes.get('new name.py')!.after]).toEqual([2]);
 expect(changes.get('old name.py')).toBe(changes.get('new name.py'));
 expect([...changes.get('added.py')!.after]).toEqual([1]);
 expect([...changes.get('deleted.py')!.before]).toEqual([1]);
 expect(changedLines('')).toHaveProperty('size',0);
});

test('unchanged job details are not downloaded during summary polling',async({page})=>{
 const calls=await fixture(page);await page.goto('/#/repositories/repo-one/jobs/job-one');
 await expect(page.getByRole('heading',{name:'Fix quoted prices'})).toBeVisible();
 const initial=calls.filter(path=>path==='/jobs/job-one').length;await page.waitForTimeout(3400);
 expect(calls.filter(path=>path==='/jobs/job-one').length).toBe(initial);
});

test('a response bound to a different snapshot shows a stale warning',async({page})=>{
 await fixture(page);await page.route('**/api/repositories/repo-one/ask',route=>route.fulfill({json:{snapshot:'old-snapshot',answer:'OUTDATED_ANSWER',citations:[]}}));
 await page.goto('/#/repositories/repo-one/repository');await page.getByLabel('Project question').fill('How does this work?');await page.getByRole('button',{name:'Ask',exact:true}).click();
 await expect(page.getByText('The project snapshot changed while this evidence was loading. Refresh the index before relying on this answer.')).toBeVisible();
 await expect(page.getByText('OUTDATED_ANSWER')).toHaveCount(0);
});

test('refresh removes retrieved evidence and answers from the previous snapshot',async({page})=>{
 await fixture(page);let snapshot='snapshot-one';
 await page.route('**/api/repositories/repo-one',route=>route.fulfill({json:{id:'repo-one',name:'Python project',snapshot,index:{files:[],edges:[],coverage:{indexed:0},gaps:[]}}}));
 await page.route('**/api/repositories/repo-one/ask',route=>route.fulfill({json:{snapshot,answer:'OLD_SNAPSHOT_ANSWER',citations:[]}}));
 await page.route('**/api/repositories/repo-one/search?*',route=>route.fulfill({json:{snapshot,mode:'fixture',items:[{path:'removed.py',start_line:1,end_line:1,content:'OLD_SOURCE',snapshot}],missing:[]}}));
 await page.route('**/api/repositories/repo-one/refresh',route=>{snapshot='snapshot-new';return route.fulfill({json:{id:'repo-one'}})});
 await page.goto('/local/repo-one/codebase');
 await page.getByLabel('Project question').fill('Trace the quote');await page.getByRole('button',{name:'Ask',exact:true}).click();
 await expect(page.getByText('OLD_SNAPSHOT_ANSWER',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Files',exact:true}).click();
 await page.getByLabel('Search source').fill('removed');await page.getByRole('button',{name:'Retrieve context'}).click();
 await expect(page.locator('.evidence')).toContainText('removed.py');
 await page.getByRole('button',{name:'Refresh index'}).click();
 await expect(page.locator('.snapshot')).toHaveText('snapshot-new');
 await expect(page.locator('.evidence')).toHaveCount(0);
 await page.getByRole('button',{name:'Overview',exact:true}).click();
 await expect(page.getByText('OLD_SNAPSHOT_ANSWER',{exact:true})).toHaveCount(0);
});

test('switching projects discards a delayed source search response',async({page})=>{
 await fixture(page);let release:()=>void=()=>{};const waiting=new Promise<void>(resolve=>release=resolve);
 await page.route('**/api/repositories/repo-one/search?*',async route=>{await waiting;await route.fulfill({json:{mode:'fixture',items:[{path:'PRIVATE_OLD_PROJECT.py',start_line:1,end_line:1,content:'private',snapshot:'snapshot-one'}]}})});
 await page.goto('/local/repo-one/codebase?tab=files');
 await page.getByLabel('Search source').fill('private');
 const pending=page.waitForRequest('**/api/repositories/repo-one/search?*');
 await page.getByRole('button',{name:'Retrieve context'}).click();await pending;
 await page.getByLabel('Switch project').selectOption('repo-two');
 await page.getByRole('button',{name:'Project codebase',exact:true}).click();
 await page.getByRole('button',{name:'Files',exact:true}).click();
 const response=page.waitForResponse('**/api/repositories/repo-one/search?*');release();await response;
 await expect(page.getByRole('button',{name:'Retrieve context'})).toBeEnabled();
 await expect(page.locator('.evidence')).toHaveCount(0);
});

test('a delayed search cannot restore evidence after a source-only snapshot change',async({page})=>{
 await fixture(page);let source='source-old',release:()=>void=()=>{};
 const waiting=new Promise<void>(resolve=>release=resolve);
 await page.route('**/api/repositories/repo-one',route=>route.fulfill({json:{id:'repo-one',name:'Python project',snapshot:'content-same',source_snapshot_id:source,index:{files:[],edges:[],coverage:{indexed:0},gaps:[]}}}));
 await page.route('**/api/repositories/repo-one/search?*',async route=>{await waiting;await route.fulfill({json:{snapshot:'source-old',mode:'fixture',items:[{path:'OLD_COMMIT.py',start_line:1,end_line:1,content:'old',snapshot:'source-old'}]}})});
 await page.goto('/local/repo-one/codebase?tab=files');
 await page.getByLabel('Search source').fill('old');
 const pending=page.waitForRequest('**/api/repositories/repo-one/search?*');
 await page.getByRole('button',{name:'Retrieve context'}).click();await pending;
 source='source-new';
 await page.getByRole('button',{name:'Project jobs',exact:true}).click();
 await page.getByRole('button',{name:/Fix quoted prices/}).click();
 await expect(page.getByRole('heading',{name:'Fix quoted prices'})).toBeVisible();
 await page.getByRole('button',{name:'Project codebase',exact:true}).click();
 await page.getByRole('button',{name:'Files',exact:true}).click();
 const response=page.waitForResponse('**/api/repositories/repo-one/search?*');release();await response;
 await expect(page.getByRole('button',{name:'Retrieve context'})).toBeEnabled();
 await expect(page.locator('.evidence')).toHaveCount(0);
});

test('syntax highlighting is present in unified and split views',async({page})=>{
 await fixture(page);await page.goto('/#/repositories/repo-one/jobs/job-one');
 await expect(page.locator('.hljs-keyword').first()).toBeVisible();
 await expect(page.locator('[aria-label="Changed files"]')).toContainText('Modified');
 await page.getByRole('button',{name:'Split view'}).click();
 await expect(page.getByText('Before',{exact:true})).toBeVisible();await expect(page.getByText('After',{exact:true})).toBeVisible();
 await expect(page.locator('.split-diff .hljs-keyword').first()).toBeVisible();
 await expect(page.locator('.split-diff .deletion code')).toHaveText('    return 10');
 await expect(page.locator('.split-diff .addition code')).toHaveText('    return 20');
 await expect(page.locator('.split-diff .deletion .diff-sign')).toHaveText('-');
 await expect(page.locator('.split-diff .addition .diff-sign')).toHaveText('+');
 await expect(page.locator('.split-diff .deletion')).toHaveCSS('background-color','rgb(52, 26, 28)');
 await expect(page.locator('.split-diff .addition')).toHaveCSS('background-color','rgb(18, 38, 30)');
 await expect(page.locator('.split-diff .diff-code-line').filter({hasText:'def total()'}).first()).toHaveCSS('background-color','rgba(0, 0, 0, 0)');
 await page.getByLabel('Colour theme').selectOption('light');
 await expect(page.locator('.split-diff .deletion')).toHaveCSS('background-color','rgb(255, 235, 233)');
 await expect(page.locator('.split-diff .addition')).toHaveCSS('background-color','rgb(218, 251, 225)');
});

test('split rows align insertions and removals and both panes scroll horizontally together',async({page})=>{
 await fixture(page);
 const long='long_value = "'+'x'.repeat(220)+'"';
 const before=['start','removed one','removed two','anchor','old value','tail',long].join('\n')+'\n';
 const after=['start','anchor','new value','extra value','tail','short value'].join('\n')+'\n';
 const diff='--- a/app.py\n+++ b/app.py\n@@ -1,7 +1,6 @@\n start\n-removed one\n-removed two\n anchor\n-old value\n+new value\n+extra value\n tail\n-'+long+'\n+short value\n';
 await page.route('**/api/jobs/job-one',route=>route.fulfill({json:{id:'job-one',repo_id:'repo-one',title:'Aligned changes',status:'Ready for review',current_revision:'aligned',revisions:[{id:'aligned',number:1,diff,files:[{path:'app.py',before,after}]}]}}));
 await page.goto('/local/repo-one/jobs/job-one');await page.getByRole('button',{name:'Split view',exact:true}).click();
 const left=page.locator('.split-diff>div').nth(0),right=page.locator('.split-diff>div').nth(1);
 await expect(left.locator('.diff-code-line')).toHaveCount(8);
 await expect(right.locator('.diff-code-line')).toHaveCount(8);
 await expect(left.locator('.diff-gap')).toHaveCount(1);
 await expect(right.locator('.diff-gap')).toHaveCount(2);
 for(const text of ['anchor','tail']){
  const a=await left.locator('code').filter({hasText:new RegExp('^'+text+'$')}).boundingBox();
  const b=await right.locator('code').filter({hasText:new RegExp('^'+text+'$')}).boundingBox();
  expect(a!.y).toBe(b!.y);
 }
 await expect(left.locator('.diff-code-line').nth(5).locator('.line-number')).toBeEmpty();
 await expect(right.locator('.diff-code-line').nth(5).locator('.line-number')).toHaveText('4');
 const scroll=(side:typeof left,value:number)=>side.locator('pre').evaluate((node,value)=>{node.scrollLeft=value},value);
 await scroll(left,180);await expect.poll(()=>right.locator('pre').evaluate(node=>node.scrollLeft)).toBe(180);
 await scroll(right,80);await expect.poll(()=>left.locator('pre').evaluate(node=>node.scrollLeft)).toBe(80);
 await scroll(right,0);await expect.poll(()=>left.locator('pre').evaluate(node=>node.scrollLeft)).toBe(0);
 await right.locator('pre').hover();
 for(let index=0;index<18;index++)await page.mouse.wheel(25,0);
 await expect.poll(()=>right.locator('pre').evaluate(node=>node.scrollLeft)).toBe(450);
 await expect.poll(()=>left.locator('pre').evaluate(node=>node.scrollLeft)).toBe(450);
 await page.setViewportSize({width:390,height:844});
 await scroll(right,250);await expect.poll(()=>left.locator('pre').evaluate(node=>node.scrollLeft)).toBe(250);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
});
