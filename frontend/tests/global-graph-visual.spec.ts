import {expect, test} from '@playwright/test';

// Synthetic relationships exercise the UI, not live Atlas or vulnerability applicability.
const graph = {
  coverage: 'unknown', truncated: false, limitations: ['Coverage remains partial.'],
  nodes: [
    {id: 'package', type: 'package', label: 'PyPI / requests'},
    {id: 'finding', type: 'entry', label: 'Synthetic advisory', entry: {
      canonical_id: 'CVE-fixture', material_revision: 2, summary: 'Synthetic source evidence.',
      freshness: 'stale', version_status: 'unsupported', source_quality: 'unreviewed',
    }},
    {id: 'source', type: 'source', label: 'osv.dev', source_url: 'https://osv.dev/vulnerability/fixture'},
  ],
  edges: [
    {id: 'affects', source: 'finding', target: 'package', type: 'affects', canonical_id: 'CVE-fixture', material_revision: 2, applicability: 'unknown'},
    {id: 'source-link', source: 'finding', target: 'source', type: 'supported_by', canonical_id: 'CVE-fixture', material_revision: 2, applicability: 'unknown'},
  ],
};

test.beforeEach(async ({page}) => {
  page.on('pageerror', error => { throw error; });
  await page.route('**/graph-component-test', route => route.fulfill({contentType: 'text/html', body: `
    <html><body><div id="root"></div><script type="module">
    import RefreshRuntime from '/@react-refresh';
    RefreshRuntime.injectIntoGlobalHook(window);
    window.$RefreshReg$ = () => {}; window.$RefreshSig$ = () => type => type;
    window.__vite_plugin_react_preamble_installed__ = true;
    const {default: React} = await import('/node_modules/.vite/deps/react.js');
    const {default: ReactDOM} = await import('/node_modules/.vite/deps/react-dom_client.js');
    const {default: Graph} = await import('/src/components/GlobalRelationships.tsx');
    await import('/src/style.css');
    ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(Graph, {
      query: async () => { const response = await fetch('/graph-data'); if (!response.ok) throw new Error('Graph unavailable'); return response.json(); }
    }));
    </script></body></html>`}));
});

test('draws actual edges and exposes keyboard-accessible evidence on narrow screens', async ({page}) => {
  await page.route('**/graph-data', route => route.fulfill({json: graph}));
  await page.goto('/graph-component-test');
  await page.getByRole('button', {name: 'View relationships'}).click();
  const diagram = page.getByRole('region', {name: 'Interactive public graph'});
  await expect(diagram.getByRole('button')).toHaveCount(3);
  await expect(diagram.locator('svg path[data-edge]')).toHaveCount(2);
  const finding = diagram.getByRole('button', {name: 'Finding: Synthetic advisory'});
  await finding.focus();
  await page.keyboard.press('Enter');
  await expect(finding).toHaveAttribute('aria-pressed', 'true');
  const details = page.getByRole('region', {name: 'Selected graph node'});
  await expect(details).toContainText('Synthetic source evidence.');
  await expect(details).toContainText('stale');
  await expect(details).toContainText('Applicability unknown');
  await diagram.getByRole('button', {name: 'Source: osv.dev'}).click();
  await expect(details.getByRole('link', {name: 'Original source'})).toHaveAttribute('href', graph.nodes[2].source_url!);
  await page.setViewportSize({width: 390, height: 844});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.evaluate(() => document.documentElement.dataset.theme = 'dark');
  await expect(diagram).toBeVisible();
});

test('clears the diagram and selection on query failure; empty graphs stay unknown', async ({page}) => {
  let mode = 'populated';
  await page.route('**/graph-data', route => mode === 'failed' ? route.fulfill({status: 503}) : route.fulfill({json: mode === 'empty' ? {...graph, nodes: [], edges: []} : graph}));
  await page.goto('/graph-component-test');
  await page.getByRole('button', {name: 'View relationships'}).click();
  await page.getByRole('button', {name: 'Finding: Synthetic advisory'}).click();
  mode = 'failed';
  await page.getByRole('button', {name: 'View relationships'}).click();
  await expect(page.getByRole('alert')).toHaveText('Graph unavailable');
  await expect(page.getByRole('region', {name: 'Interactive public graph'})).toHaveCount(0);
  await expect(page.getByRole('region', {name: 'Selected graph node'})).toHaveCount(0);
  mode = 'empty';
  await page.getByRole('button', {name: 'View relationships'}).click();
  await expect(page.getByText('No relationships found. Coverage remains unknown.')).toBeVisible();
});
