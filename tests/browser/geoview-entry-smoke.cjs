const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_PACKAGE || 'playwright');
const baseUrl = process.env.GEOVIEW_UI_URL || 'http://127.0.0.1:4502';
const out = process.env.GUI_AUDIT_OUTPUT || path.resolve('artifacts/geoview-entry');

async function main() {
  await fs.mkdir(out, { recursive: true });
  const browser = await chromium.launch({ headless: false });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  let releaseScripts;
  const scriptGate = new Promise(resolve => { releaseScripts = resolve; });
  await context.route('**/*.js', async route => { await scriptGate; await route.continue(); });
  await context.route('**/api/**', async route => {
    const endpoint = new URL(route.request().url()).pathname;
    const headers = { 'Access-Control-Allow-Origin': baseUrl, 'Access-Control-Allow-Credentials': 'true' };
    if (endpoint === '/api/auth/session') {
      await new Promise(resolve => setTimeout(resolve, 1000));
      await route.fulfill({ headers, json: { code: 0, success: true, data: { authenticated: true, username: '视觉测试管理员' } } });
    } else if (endpoint.includes('history')) {
      await route.fulfill({ headers, json: { code: 0, success: true, data: [], count: 0 } });
    } else {
      await route.fulfill({ status: 404, headers, json: { success: false, msg: '未提供测试 fixture' } });
    }
  });
  try {
    await page.goto(`${baseUrl}/segmentation?project_id=42`, { waitUntil: 'commit' });
    await page.locator('#app').waitFor({ state: 'attached' });
    await page.screenshot({ path: path.join(out, 'geoview-entry-before-js.png') });
    const initial = await page.evaluate(() => ({
      oldSplash: Boolean(document.querySelector('#loader-wrapper')),
      background: getComputedStyle(document.body).backgroundColor,
      backgroundImage: getComputedStyle(document.body).backgroundImage,
    }));
    const sessionPending = page.waitForRequest(request => new URL(request.url()).pathname === '/api/auth/session');
    releaseScripts();
    await sessionPending;
    await page.screenshot({ path: path.join(out, 'geoview-entry-session.png') });
    await page.locator('.geoview-shell').waitFor();
    await page.locator('.tiff-upload-card').waitFor();
    await page.evaluate(async () => {
      await Promise.all(document.getAnimations().filter(animation => animation.effect?.getTiming().iterations !== Infinity).map(animation => animation.finished.catch(() => {})));
    });
    await page.screenshot({ path: path.join(out, 'geoview-entry-ready.png') });
    await fs.writeFile(path.join(out, 'geoview-entry-results.json'), JSON.stringify(initial, null, 2));
    assert.equal(initial.oldSplash, false, 'JS 下载前不能显示旧蓝色启动页');
    assert.equal(initial.background, 'rgb(242, 246, 247)', '首帧背景必须与平台一致');
    console.log(JSON.stringify({ passed: true, initial }));
  } finally {
    releaseScripts();
    await context.close();
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
