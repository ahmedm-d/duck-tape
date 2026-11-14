import {expect, test} from '@playwright/test';
import {fixture} from '../fixtures';

test.beforeEach(async ({page}) => {
  await fixture(page);
});

test('keeps the hero minimal and presents the requested scroll sequence', async ({page}) => {
  await page.goto('/');
  await expect(page.getByRole('heading', {level: 1})).toHaveText('Duck Tape is on Duck Tape');
  await expect(page.locator('.landing-hero p')).toHaveCount(0);
  await expect(page.locator('.landing-hero h1')).toHaveCSS('color', 'rgb(255, 255, 255)');
  await expect(page.getByRole('link', {name: 'Log in', exact: true})).toHaveAttribute('href', '/dashboard');
  await expect(page.locator('.landing-header img')).toHaveAttribute('src', '/landing/logo.png');
  await expect(page.locator('.landing-video video')).toHaveCSS('filter', 'none');
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

test('plays the supplied video and supports pausing and resuming', async ({page}) => {
  await page.goto('/');
  const video = page.locator('.landing-video video');
  await expect(video).toHaveAttribute('src', '/landing/hero.mp4');
  await expect.poll(() => video.evaluate((node: HTMLVideoElement) => node.readyState)).toBeGreaterThanOrEqual(2);
  await expect.poll(() => video.evaluate((node: HTMLVideoElement) => node.paused)).toBe(false);
  await page.getByRole('button', {name: 'Pause video'}).click();
  await expect.poll(() => video.evaluate((node: HTMLVideoElement) => node.paused)).toBe(true);
  await page.getByRole('button', {name: 'Play video'}).press('Enter');
  await expect.poll(() => video.evaluate((node: HTMLVideoElement) => node.paused)).toBe(false);
});

test('respects reduced motion and allows an explicit play choice', async ({page}) => {
  await page.emulateMedia({reducedMotion: 'reduce'});
  await page.goto('/');
  const video = page.locator('.landing-video video');
  await expect(video).toHaveAttribute('poster', '/landing/hero-poster.jpg');
  await expect(page.getByRole('button', {name: 'Play video'})).toBeVisible();
  expect(await video.evaluate((node: HTMLVideoElement) => node.paused)).toBe(true);
  await page.getByRole('button', {name: 'Play video'}).click();
  await expect(page.getByRole('button', {name: 'Pause video'})).toBeVisible();
  await page.emulateMedia({reducedMotion: 'no-preference'});
  await page.emulateMedia({reducedMotion: 'reduce'});
  await expect(page.getByRole('button', {name: 'Play video'})).toBeVisible();
});

test('leaves a poster when the video is unavailable', async ({page}) => {
  await page.route('**/landing/hero.mp4', route => route.abort());
  await page.goto('/');
  await expect(page.locator('.landing-video video')).toHaveAttribute('poster', '/landing/hero-poster.jpg');
  await expect.poll(() => page.locator('.landing-video video').evaluate((node: HTMLVideoElement) => node.error?.code)).toBeTruthy();
  await expect(page.getByRole('button', {name: /^(Play|Pause) video$/})).toHaveCount(0);
});

test('keeps the play control available when autoplay is blocked', async ({page}) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    HTMLMediaElement.prototype.play = () => Promise.reject(new DOMException('Autoplay blocked', 'NotAllowedError'));
  });
  await page.goto('/');
  await expect(page.getByRole('button', {name: 'Play video'})).toBeVisible();
  await page.getByRole('button', {name: 'Play video'}).click();
  await expect(page.getByRole('button', {name: 'Play video'})).toBeVisible();
  expect(errors).toEqual([]);
});

for (const {width, height} of [{width: 390, height: 900}, {width: 1440, height: 900}, {width: 844, height: 390}, {width: 320, height: 568}]) {
  test(`fits a ${width}×${height} viewport with the video at the bottom of the first screen`, async ({page}) => {
    await page.setViewportSize({width, height});
    await page.addInitScript(() => localStorage.setItem('steward-theme', 'light'));
    await page.goto('/');
    const hero = await page.locator('.landing-opening').boundingBox();
    const video = await page.locator('.landing-video').boundingBox();
    expect(hero!.height).toBe(height);
    expect(video!.y).toBeGreaterThan(100);
    expect(video!.y + video!.height).toBeLessThanOrEqual(height);
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
