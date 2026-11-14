import {expect, test} from '@playwright/test';

// Synthetic public records: browser contract checks, not live cloud evidence.
const result = {
  ecosystem: 'PyPI', package: 'example', coverage: 'unknown', truncated: false,
  entries: [{canonical_id: 'OSV-fixture', aliases: ['CVE-fixture'], kind: 'vulnerability',
    title: 'Synthetic advisory', summary: 'Public evidence only',
    source_url: 'https://osv.dev/vulnerability/OSV-fixture', source_quality: 'unreviewed',
    published_at: '2026-01-01T00:00:00Z', retrieved_at: '2026-01-02T00:00:00Z',
    source_content_hash: 'a'.repeat(64), material_revision: 2, freshness: 'stale',
    withdrawn_at: '2026-02-01T00:00:00Z', version_status: 'unsupported',
    affected: [{ecosystem: 'PyPI', package: 'example', versions: [], ranges: [], fixed_versions: []}],
    conditions: [], evidence: ['Synthetic source attribution'], limitations: ['Unsupported version rules']}],
  limitations: ['This catalogue does not establish complete coverage'],
  source_status: [{operation: 'ingest', identity: 'https://api.osv.dev/v1/vulns/OSV-fixture',
    state: 'failed', detail: 'Public source ingestion failed', attempted_at: '2026-11-14T00:00:00Z'}],
};

test.beforeEach(async ({page}) => {
  page.on('pageerror', error => { throw error; });
  await page.route('**/catalogue-component-test', route => route.fulfill({contentType: 'text/html', body: `
    <html><body><div id="root"></div><script type="module">
    import RefreshRuntime from '/@react-refresh';
    RefreshRuntime.injectIntoGlobalHook(window);
    window.$RefreshReg$ = () => {}; window.$RefreshSig$ = () => type => type;
    window.__vite_plugin_react_preamble_installed__ = true;
    const {default: React} = await import('/node_modules/.vite/deps/react.js');
    const {default: ReactDOM} = await import('/node_modules/.vite/deps/react-dom_client.js');
    const {default: GlobalCatalogue} = await import('/src/components/GlobalCatalogue.tsx');
    await import('/src/style.css');
    ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(GlobalCatalogue, {
      query: async params => {
        const response = await fetch('/test-catalogue-query?' + new URLSearchParams(params));
        if (!response.ok) throw new Error('Catalogue unavailable');
        return response.json();
      }
    }));
    </script></body></html>`}));
});

test('shows source, revision and uncertainty with exact bounded queries', async ({page}) => {
  await page.route('**/test-catalogue-query?*', route => {
    const params = new URL(route.request().url()).searchParams;
    expect(params.get('ecosystem')).toBe('PyPI');
    expect(params.get('package')).toBe('example');
    expect(params.get('limit')).toBe('25');
    return route.fulfill({json: result});
  });
  await page.goto('/catalogue-component-test');
  await page.getByLabel('Ecosystem', {exact: true}).fill('PyPI');
  await page.getByLabel('Package', {exact: true}).fill('example');
  await page.getByRole('button', {name: 'Search catalogue'}).click();
  await expect(page.getByRole('heading', {name: 'Synthetic advisory'})).toBeVisible();
  await expect(page.getByText('Coverage unknown', {exact: true})).toBeVisible();
  await expect(page.getByText('Stale', {exact: true})).toBeVisible();
  await expect(page.getByText('Withdrawn', {exact: true})).toBeVisible();
  await expect(page.getByText('Version rules unsupported', {exact: true})).toBeVisible();
  await expect(page.getByText('ingest · failed', {exact: true})).toBeVisible();
  await expect(page.getByRole('link', {name: 'Original source'})).toHaveAttribute('href', result.entries[0].source_url);
  await page.getByText('Evidence and version rules').click();
  await expect(page.getByText('Synthetic source attribution')).toBeVisible();
  await page.setViewportSize({width: 390, height: 844});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.evaluate(() => document.documentElement.dataset.theme = 'dark');
  await expect(page.getByRole('heading', {name: 'Synthetic advisory'})).toBeVisible();
});

test('empty results remain unknown and failed queries clear previous results', async ({page}) => {
  let fail = false;
  await page.route('**/test-catalogue-query?*', route => fail
    ? route.fulfill({status: 503, body: 'unavailable'})
    : route.fulfill({json: {...result, entries: []}}));
  await page.goto('/catalogue-component-test');
  await page.getByLabel('Ecosystem', {exact: true}).fill('PyPI');
  await page.getByLabel('Package', {exact: true}).fill('unlisted');
  await page.getByRole('button', {name: 'Search catalogue'}).click();
  await expect(page.getByText('No catalogue entries found. This is not a clean scan.')).toBeVisible();
  fail = true;
  await page.getByRole('button', {name: 'Search catalogue'}).click();
  await expect(page.getByRole('alert')).toHaveText('Catalogue unavailable');
  await expect(page.getByText('No catalogue entries found. This is not a clean scan.')).toHaveCount(0);
});

test('failed refresh never labels a retained entry fresh', async ({page}) => {
  await page.route('**/test-catalogue-query?*', route => route.fulfill({json: {
    ...result, entries: [{...result.entries[0], freshness: 'unknown', source_status: 'failed', withdrawn_at: null}],
  }}));
  await page.goto('/catalogue-component-test');
  await page.getByLabel('Ecosystem', {exact: true}).fill('PyPI');
  await page.getByLabel('Package', {exact: true}).fill('example');
  await page.getByRole('button', {name: 'Search catalogue'}).click();
  await expect(page.getByText('Freshness unknown', {exact: true})).toBeVisible();
  await expect(page.getByText('Fresh', {exact: true})).toHaveCount(0);
  await expect(page.getByText('Revalidate this source before considering a new proposal.')).toBeVisible();
});
