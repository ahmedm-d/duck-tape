import {expect, test} from '@playwright/test';
import {fixture} from './fixtures';
import {readFileSync} from 'node:fs';

const advisory = JSON.parse(readFileSync(new URL('../../integrations/global/fixtures/global-vulnerability.json', import.meta.url), 'utf8'));

test('global catalogue route survives reload and repository changes', async ({page}) => {
  await fixture(page);
  await page.route('**/api/global-catalogue?*', route => {
    const params = new URL(route.request().url()).searchParams;
    expect(params.get('ecosystem')).toBe('PyPI');
    expect(params.get('package')).toBe('unknown-example');
    expect(params.get('limit')).toBe('25');
    expect(params.has('repo_id')).toBe(false);
    return route.fulfill({json: {ecosystem: 'PyPI', package: 'unknown-example', coverage: 'unknown', entries: [], limitations: [], truncated: false, source_status: []}});
  });
  await page.goto('/dashboard');
  await page.getByRole('button', {name: 'Global catalogue', exact: true}).click();
  await expect(page).toHaveURL(/\/global-catalogue$/);
  await page.reload();
  await expect(page.getByRole('heading', {name: 'Global catalogue', exact: true})).toBeVisible();
  await page.getByLabel('Ecosystem', {exact: true}).fill('PyPI');
  await page.getByLabel('Package', {exact: true}).fill('unknown-example');
  await page.getByRole('button', {name: 'Search catalogue'}).click();
  await expect(page.getByText('No catalogue entries found. This is not a clean scan.')).toBeVisible();
  await page.getByRole('button', {name: 'Dashboard', exact: true}).click();
  await page.goBack();
  await expect(page.getByRole('heading', {name: 'Global catalogue', exact: true})).toBeVisible();
});

test('global catalogue opens without an imported repository', async ({page}) => {
  await fixture(page);
  await page.route('**/api/repositories?summary=true', route => route.fulfill({json: []}));
  await page.goto('/global-catalogue');
  await expect(page.getByRole('heading', {name: 'Global catalogue', exact: true})).toBeVisible();
  await expect(page).toHaveURL(/\/global-catalogue$/);
  await page.setViewportSize({width: 390, height: 844});
  await page.getByRole('button', {name: 'Toggle theme'}).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('relationship graph exposes newer withdrawn and failed-source evidence', async ({page}) => {
  await fixture(page);
  await page.route('**/api/global-catalogue?*', route => route.fulfill({json: {
    ecosystem: 'PyPI', package: 'duck-tape-fixture', coverage: 'unknown', truncated: false,
    entries: [{...advisory, freshness: 'fresh', withdrawn_at: null, version_status: 'exact_versions'}],
    limitations: [], source_status: [],
  }}));
  await page.route('**/api/global-catalogue/graph?*', route => route.fulfill({json: {
    coverage: 'unknown', truncated: false, limitations: [],
    nodes: [
      {id: 'entry', type: 'entry', label: advisory.title, entry: {...advisory,
        freshness: 'unknown', withdrawn_at: '2026-11-14T01:00:00Z',
        source_status: 'failed', version_status: 'unsupported'}},
      {id: 'package', type: 'package', label: 'PyPI / duck-tape-fixture'},
    ],
    edges: [{id: 'edge', source: 'entry', target: 'package', type: 'affects',
      canonical_id: advisory.canonical_id, material_revision: 2, applicability: 'unknown'}],
  }}));
  await page.goto('/global-catalogue');
  await page.getByLabel('Ecosystem', {exact: true}).fill('PyPI');
  await page.getByLabel('Package', {exact: true}).fill('duck-tape-fixture');
  await page.getByRole('button', {name: 'Search catalogue'}).click();
  await expect(page.getByText('Fresh', {exact: true})).toBeVisible();
  await page.getByRole('button', {name: 'View relationships'}).click();
  const graph = page.getByRole('region', {name: 'Public relationship graph'});
  await expect(graph.getByText('Withdrawn', {exact: true})).toBeVisible();
  await expect(graph.getByText('Freshness unknown', {exact: true})).toBeVisible();
  await expect(graph.getByText('Source refresh failed', {exact: true})).toBeVisible();
  await expect(graph.getByText('Version rules unsupported', {exact: true})).toBeVisible();
  await expect(graph.getByText('Revalidate this source before considering a new proposal.')).toBeVisible();
});
