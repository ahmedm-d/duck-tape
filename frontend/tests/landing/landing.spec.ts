import {expect, test} from '@playwright/test';
import {fixture} from '../fixtures';

test.beforeEach(async ({page}) => {
  await fixture(page);
});

test('keeps the hero minimal and presents the requested scroll sequence', async ({page}) => {
  await page.goto('/');
  await expect(page.getByRole('heading', {level: 1})).toHaveText('Duck Tape holds your codebase together.');
  await expect(page.locator('.landing-hero p')).toHaveCount(0);
  await expect(page.locator('.landing-hero h1')).toHaveCSS('color', 'rgb(255, 255, 255)');
  await expect(page.getByRole('link', {name: 'Log in', exact: true})).toHaveAttribute('href', '/login');
  await expect(page.locator('.landing-header img')).toHaveAttribute('src', '/landing/logo.png');
  await expect(page.locator('.landing-hero-art')).toHaveAttribute('src', '/landing/hero-still.png');
  await expect.poll(() => page.locator('.landing-hero-art').evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);
  await expect(page.locator('video')).toHaveCount(0);
  const sections = await page.locator('.landing main > section').evaluateAll(nodes => nodes.map(node => node.id));
  expect(sections).toEqual(['workspace', 'workflow', 'benchmarks']);
  await expect(page.locator('iframe')).toHaveAttribute('src', '/dashboard?preview=1');
  await expect(page.frameLocator('iframe').getByRole('heading', {name: 'Dashboard', exact: true})).toBeVisible();
  await expect(page.locator('.landing-flow > li')).toHaveCount(4);
  await expect(page.locator('.landing-flow-sources')).toContainText('Tickets');
  await expect(page.frameLocator('iframe').locator('html')).toHaveClass(/landing-demo/);
  await expect(page.frameLocator('iframe').locator('body')).toHaveCSS('color', 'rgb(240, 240, 245)');
  await expect(page.getByText('Illustrative data, not measured results.')).toBeVisible();
  await page.getByRole('navigation', {name: 'Main navigation'}).getByRole('link', {name: 'Workflow'}).click();
  await expect(page.locator('#workflow')).toBeInViewport();
  await page.getByRole('link', {name: /Start creating/}).click();
  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.locator('html')).not.toHaveClass(/landing-demo/);
});

for (const {width, height} of [{width: 390, height: 900}, {width: 1440, height: 900}, {width: 768, height: 1024}, {width: 701, height: 900}, {width: 700, height: 700}, {width: 844, height: 390}, {width: 320, height: 568}]) {
  test(`fits a ${width}×${height} viewport with readable hero copy and the supplied still`, async ({page}) => {
    await page.setViewportSize({width, height});
    await page.addInitScript(() => localStorage.setItem('steward-theme', 'light'));
    await page.goto('/');
    const logo = await page.locator('.landing-logo').boundingBox();
    const navigation = await page.locator('.landing-nav').boundingBox();
    expect(logo!.x + logo!.width).toBeLessThan(navigation!.x);
    const hero = await page.locator('.landing-opening').boundingBox();
    const copy = await page.locator('.landing-hero-copy').boundingBox();
    const art = await page.locator('.landing-hero-art').boundingBox();
    expect(hero!.height).toBeGreaterThanOrEqual(height);
    expect(copy!.x).toBeLessThan(width / 4);
    if (width <= 700 || (width <= 900 && height >= 651)) expect(copy!.y + copy!.height).toBeLessThanOrEqual(art!.y + 10);
    else expect(copy!.x + copy!.width).toBeLessThan(width * .6);
    expect(copy!.y + copy!.height).toBeLessThan(hero!.height);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    expect(await page.evaluate(() => localStorage.getItem('steward-theme'))).toBe('light');
    await expect(page.locator('.landing')).toHaveCSS('background-color', 'rgb(5, 6, 10)');
    await page.getByRole('link', {name: 'Skip to content'}).focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('#workspace')).toBeInViewport();
    const preview = await page.locator('.landing-preview-viewport').boundingBox();
    const frame = await page.locator('.landing-preview iframe').boundingBox();
    expect(frame!.width).toBeLessThanOrEqual(preview!.width + 1);
  });
}
