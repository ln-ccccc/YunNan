const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const { chromium } = require(process.env.PLAYWRIGHT_PACKAGE || 'playwright');
const baseUrl = process.env.GEOVIEW_UI_URL || 'http://127.0.0.1:4402';
const out = process.env.GUI_AUDIT_OUTPUT || path.join(os.tmpdir(), 'yunnan-liquid-glass-gui');
const checks = [], errors = [], missing = [], knownBugs = [], consoleErrors = [], failedRequests = [];
const thumb = '/visual-audit/image.svg';
const thumbSvg = '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300"><rect width="300" height="300" fill="#cfddd4"/><path d="M0 220 100 80l90 130 55-70 55 110v50H0Z" fill="#7da38c"/><text x="50" y="270" fill="#16383c" font-size="20">合成测试影像</text></svg>';
const classes = ['草地', '林地', '建筑', '道路', '裸地', '水体'].map((name, index) => ({ class_code: index, class_name_zh: name, class_name: ['grassland','forest','building','road','bareground','water'][index] }));
async function main() {
  await fs.mkdir(out, { recursive: true });
  const browser = await chromium.launch({ headless: false, executablePath: process.env.AUDIT_BROWSER_EXECUTABLE });
  const context = await browser.newContext({ reducedMotion: 'no-preference', viewport: { width: 1440, height: 1000 }, recordVideo: { dir: out, size: { width: 1440, height: 1000 } } });
  await context.tracing.start({ screenshots: true, snapshots: true });
  await context.route('**/visual-audit/image.svg', route => route.fulfill({ contentType: 'image/svg+xml', body: thumbSvg }));
  await context.route('**/api/**', async route => {
    const endpoint = new URL(route.request().url()).pathname;
    const headers = { 'Access-Control-Allow-Origin': baseUrl, 'Access-Control-Allow-Credentials': 'true', 'Access-Control-Allow-Headers': 'content-type', 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS' };
    if (route.request().method() === 'OPTIONS') { await route.fulfill({ status: 204, headers }); return; }
    let data;
    if (endpoint === '/api/auth/session') data = { authenticated: true, username: '视觉测试管理员' };
    else if (endpoint.includes('history')) data = [{ record_id: 'visual-1', id: 1, type: '地物分类', before_img: thumb, after_img: thumb, data: { fid: 1, year: 2024, project_id: 42, result_id: 142, file: '合成测试影像' } }];
    else if (endpoint.endsWith('/classification-results/142/revisions')) data = { revisions: [{ revision_no: 0, source: 'auto', feature_count: 1, created_at: '2026-10-09T10:00:00Z' }] };
    else if (endpoint.endsWith('/classification-results/142')) data = { result_id: 142, project_id: 42, mine_fid: 1, year: 2024, vector_status: 'ready', current_revision_no: 0, classes, current_feature_collection: { type: 'FeatureCollection', features: [{ type: 'Feature', id: 'auto-1', properties: { class_code: 1, class_name: 'forest' }, geometry: { type: 'Polygon', coordinates: [[[102.70,25.05],[102.72,25.05],[102.72,25.07],[102.70,25.07],[102.70,25.05]]] } }] } };
    else { missing.push(endpoint); await route.fulfill({ status: 404, headers, json: { success: false, code: 1, msg: '未提供测试 fixture' } }); return; }
    await route.fulfill({ headers, json: { code: 0, success: true, data, count: Array.isArray(data) ? data.length : 0 } });
  });
  const page = await context.newPage();
  await page.bringToFront();
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') consoleErrors.push({ text: message.text(), location: message.location() }); });
  page.on('requestfailed', request => failedRequests.push({ url: request.url(), error: request.failure()?.errorText }));
  let dialogs = 0; page.on('dialog', async dialog => { dialogs++; await dialog.dismiss(); });
  const settle = () => page.evaluate(async () => {
    for (let pass = 0; pass < 2; pass++) {
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      await Promise.all(document.getAnimations().filter(animation => animation.effect?.getTiming().iterations !== Infinity).map(animation => animation.finished.catch(() => {})));
    }
  });
  const shot = async name => { await settle(); await page.screenshot({ path: path.join(out, name) }); };
  try {
    await page.goto(`${baseUrl}/segmentation?project_id=42`);
    await page.locator('.geoview-shell').waitFor();
    await page.locator('.tiff-upload-card').waitFor();
    await shot('geoview-after-1440.png');
    const help = page.locator('.format-help .el-collapse-item__header');
    await help.click(); await page.locator('.format-help-body').waitFor();
    await help.click(); await settle();
    await page.locator('input[type=file]:not([webkitdirectory])').setInputFiles({ name: 'synthetic-selection.tif', mimeType: 'image/tiff', buffer: Buffer.from('II*\0synthetic-selection-only') });
    await page.locator('.selected-files').waitFor();
    await page.locator('.clear-queue button').click();
    checks.push('上传格式面板展开/收起；选择文件/清空（未上传或推理）');

    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator('.geoview-sidebar.el-menu--collapse').waitFor();
    await settle();
    const collapse = page.locator('.platform-header .icon-menu');
    await collapse.click();
    assert.ok(await page.locator('.geoview-shell').evaluate(node => node.classList.contains('is-nav-expanded')));
    await shot('geoview-navigation-390.png');
    await collapse.click();
    await page.locator('.geoview-sidebar.el-menu--collapse').waitFor();
    assert.equal(await collapse.getAttribute('aria-expanded'), 'false');
    await shot('geoview-after-390.png');
    assert.ok((await page.locator('.main-ctx').boundingBox()).width > 280, '收起导航后主内容应保持可用宽度');
    for (const label of ['地表覆盖分类', '光谱指数计算']) {
      const item = page.getByRole('menuitem', { name: label, exact: true });
      const icon = await item.locator(':scope > .iconfont').boundingBox();
      const rail = await page.locator('.geoview-shell > .el-aside').boundingBox();
      assert.ok(Math.abs(icon.x + icon.width / 2 - rail.x - rail.width / 2) < 3, '折叠导航图标应居中且不被裁切');
    }
    assert.ok((await collapse.boundingBox()).x >= 0);
    checks.push('390px 导航展开后仍可关闭，图标导航可达');
    await page.setViewportSize({ width: 1440, height: 1000 });
    await settle();
    const spectral = page.locator('.geoview-sidebar .el-menu-item').nth(1);
    const segmentation = page.locator('.geoview-sidebar .el-menu-item').nth(0);
    await page.evaluate(() => {
      window.__auditFrames = []; window.__auditLongTasks = []; window.__auditRunning = true;
      let last;
      const tick = now => { if (last) window.__auditFrames.push(now - last); last = now; if (window.__auditRunning) requestAnimationFrame(tick); };
      requestAnimationFrame(tick);
      new PerformanceObserver(list => window.__auditLongTasks.push(...list.getEntries().map(entry => entry.duration))).observe({ type: 'longtask', buffered: false });
    });
    for (let round = 0; round < 5; round++) { await spectral.click(); await segmentation.click(); }
    await spectral.click();
    await page.waitForURL('**/spectralindices*');
    await page.locator('.option-row .el-form-item').first().waitFor();
    await settle();
    const coldMetrics = await page.evaluate(() => {
      window.__auditRunning = false;
      const frames = window.__auditFrames.slice(2).sort((a, b) => a - b);
      return { frameCount: frames.length, frameIntervalMedianMs: frames[Math.floor(frames.length * 0.5)], frameIntervalP95Ms: frames[Math.floor(frames.length * 0.95)], frameIntervalMaxMs: frames.at(-1), framesOver50ms: frames.filter(value => value > 50).length, longTasks: window.__auditLongTasks };
    });
    await page.evaluate(() => {
      window.__auditFrames = []; window.__auditLongTasks = []; window.__auditRunning = true;
      let last;
      const tick = now => { if (last) window.__auditFrames.push(now - last); last = now; if (window.__auditRunning) requestAnimationFrame(tick); };
      requestAnimationFrame(tick);
    });
    for (let round = 0; round < 11; round++) {
      await segmentation.click(); await page.waitForTimeout(65);
      await spectral.click(); await page.waitForTimeout(65);
    }
    await page.locator('.option-row .el-form-item').first().waitFor(); await settle();
    const metrics = await page.evaluate(() => {
      window.__auditRunning = false;
      const frames = window.__auditFrames.slice(2).sort((a, b) => a - b);
      return { frameCount: frames.length, frameIntervalMedianMs: frames[Math.floor(frames.length * 0.5)], frameIntervalP95Ms: frames[Math.floor(frames.length * 0.95)], frameIntervalMaxMs: frames.at(-1), framesOver50ms: frames.filter(value => value > 50).length, longTasks: window.__auditLongTasks };
    });
    assert.equal(await page.locator('.option-row .el-form-item').count(), 6);
    await shot('geoview-spectral-1440.png');
    for (const width of [390, 1024]) {
      await page.setViewportSize({ width, height: 1000 }); await settle();
      const layout = await page.evaluate(() => ({ viewport: innerWidth, page: document.documentElement.scrollWidth, main: document.querySelector('.main-ctx').scrollWidth, client: document.querySelector('.main-ctx').clientWidth }));
      assert.ok(layout.page <= layout.viewport + 1 && layout.main <= layout.client + 1, `光谱页面横向溢出: ${JSON.stringify(layout)}`);
      await shot(`geoview-spectral-${width}.png`);
    }
    checks.push('11次快速+22次常规连续工具页切换；光谱6项参数与390/1024px布局');
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.locator('.geoview-sidebar:not(.el-menu--collapse)').waitFor();
    await settle();
    await segmentation.click();
    await page.waitForURL('**/segmentation*');
    await page.locator('#sub-title').filter({ hasText: '地物分类' }).waitFor();
    await page.locator('.img-display').first().waitFor();
    await page.locator('.img-display').first().scrollIntoViewIfNeeded();
    await page.locator('.img-display img').first().click();
    await page.locator('.el-image-viewer__wrapper').waitFor();
    await shot('geoview-image-preview.png');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    const previewMotion = await page.locator('.el-image-viewer__wrapper').evaluate(node => ({ transition: getComputedStyle(node).transitionDuration, animation: getComputedStyle(node).animationDuration }));
    assert.ok(previewMotion.transition.split(',').every(value => parseFloat(value) <= 0.00001));
    await page.keyboard.press('Escape');
    await page.locator('.el-image-viewer__wrapper').waitFor({ state: 'detached' });
    checks.push('图片真实点击预览/退出，teleport预览减少动态效果');
    await page.emulateMedia({ reducedMotion: 'no-preference' });

    await page.goto(`${baseUrl}/classification-results/editor?project_id=42&result_id=142`);
    await page.locator('.classification-map canvas').waitFor();
    await page.locator('.mapbox-gl-draw_polygon').waitFor();
    await shot('geoview-editor-1440.png');
    for (const width of [1024, 390]) {
      await page.setViewportSize({ width, height: 1000 }); await settle();
      await page.locator('.geoview-sidebar.el-menu--collapse').waitFor();
      await page.locator('.sidebar-panels').waitFor({ state: 'detached' });
      await shot(`geoview-editor-${width}.png`);
    }
    await page.setViewportSize({ width: 1440, height: 1000 }); await settle();
    const canvas = page.locator('.classification-map canvas');
    await canvas.scrollIntoViewIfNeeded();
    const bounds = await canvas.boundingBox();
    await page.mouse.click(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
    await page.locator('.selection-count').waitFor();
    await page.locator('.class-option').first().click();
    await page.getByText('存在未保存编辑', { exact: false }).waitFor();
    const beforeReloadDialogs = dialogs;
    await page.getByRole('button', { name: '重新加载', exact: true }).click();
    await page.getByText('当前版本已保存', { exact: false }).waitFor();
    if (dialogs === beforeReloadDialogs) knownBugs.push('未保存矢量改类后点击重新加载，未确认即丢弃；既有数据保护缺陷，已GUI复现');
    checks.push('WebGL画布真实点选、改类；编辑器三种尺寸；既有丢弃编辑缺陷取证（未保存服务器）');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    const reduced = await page.locator('.platform-header .icon-menu').evaluate(node => getComputedStyle(node).transitionDuration);
    assert.ok(reduced.split(',').every(value => parseFloat(value) <= 0.00001));
    checks.push('GeoView减少动态效果仍可操作');
    assert.deepEqual(errors, [], '浏览器运行时异常');
    assert.deepEqual([...new Set(missing)], [], '未经fixture覆盖的API');
    await fs.writeFile(path.join(out, 'geoview-gui-results.json'), JSON.stringify({ scope: '可见Chromium+HTTP fixture；真实WebGL点选，不代表实际上传/GPU/写库', browser: browser.version(), checks, metrics, coldMetrics, knownBugs, errors, missing, consoleErrors, failedRequests }, null, 2));
    console.log(JSON.stringify({ checks: checks.length, metrics, coldMetrics, knownBugs, errors, missing, consoleErrors, failedRequests, out }, null, 2));
  } catch (error) {
    await page.screenshot({ path: path.join(out, 'geoview-failure.png') });
    await fs.writeFile(path.join(out, 'geoview-gui-failure.json'), JSON.stringify({ checks, url: page.url(), error: error.message, errors, missing }, null, 2));
    throw error;
  } finally { await context.tracing.stop({ path: path.join(out, 'geoview-trace.zip') }); await context.close(); await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
