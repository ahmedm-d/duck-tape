import {test,expect} from '@playwright/test';
import {fixture} from './fixtures';

test('repository summaries disclose mode and open exact cited source lines',async({page})=>{
 await fixture(page);
 await page.route('**/api/repositories/repo-one/summaries',route=>route.fulfill({json:[{id:'summary-one',kind:'repository',scope:'repository',mode:'structural fallback',summary:'Pricing is defined in pricing.py.',evidence:[{path:'pricing.py',start_line:2,end_line:2,snapshot_id:'snapshot-one'}],missing:['Semantic synthesis unavailable']}]}));
 await page.goto('/#/repositories/repo-one/repository');
 await expect(page.getByRole('heading',{name:'Evidence-linked summaries'})).toBeVisible();
 await expect(page.getByText('Pricing is defined in pricing.py.',{exact:true})).toBeVisible();
 await expect(page.getByText('structural fallback',{exact:true})).toBeVisible();
 await page.locator('.summary-card .evidence').click();
 await expect(page.locator('.source-pane [data-source-line="2"]')).toHaveClass(/cited-line/);
});
