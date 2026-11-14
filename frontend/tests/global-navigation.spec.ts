import {expect, test} from '@playwright/test';
import {fixture} from './fixtures';

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
  await page.goto('/');
  await page.getByRole('button', {name: 'Global catalogue', exact: true}).click();
  await expect(page).toHaveURL(/#\/global-catalogue$/);
  await page.getByRole('combobox', {name: 'Repository', exact: true}).selectOption('repo-two');
  await expect(page).toHaveURL(/#\/global-catalogue$/);
  await page.reload();
  await expect(page.getByRole('heading', {name: 'Global catalogue', exact: true})).toBeVisible();
  await page.getByLabel('Ecosystem', {exact: true}).fill('PyPI');
  await page.getByLabel('Package', {exact: true}).fill('unknown-example');
  await page.getByRole('button', {name: 'Search catalogue'}).click();
  await expect(page.getByText('No catalogue entries found. This is not a clean scan.')).toBeVisible();
  await page.getByRole('button', {name: 'Jobs', exact: true}).click();
  await page.goBack();
  await expect(page.getByRole('heading', {name: 'Global catalogue', exact: true})).toBeVisible();
});

test('global catalogue opens without an imported repository', async ({page}) => {
  await fixture(page);
  await page.route('**/api/repositories?summary=true', route => route.fulfill({json: []}));
  await page.goto('/#/global-catalogue');
  await expect(page.getByRole('heading', {name: 'Global catalogue', exact: true})).toBeVisible();
  await expect(page).toHaveURL(/#\/global-catalogue$/);
  await page.setViewportSize({width: 390, height: 844});
  await page.getByRole('button', {name: 'Toggle theme'}).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
