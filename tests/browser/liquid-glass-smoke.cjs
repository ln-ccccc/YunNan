// 独立 GUI 验收：HTTP fixture 只在测试浏览器内拦截，不写真实项目数据。
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const { chromium } = require(process.env.PLAYWRIGHT_PACKAGE || 'playwright');

const baseUrl = process.env.MINER_UI_URL || 'http://127.0.0.1:4401';
const out = process.env.GUI_AUDIT_OUTPUT || path.join(os.tmpdir(), 'yunnan-liquid-glass-gui');
const fixtureRoot = path.resolve(__dirname, '../fixtures/project-hub-v1');
const checks = [];
const errors = [];
const missing = [];
const requests = [];
const consoleErrors = [], failedRequests = [];
let authenticated = true;
const thumb = 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="75"><rect width="100" height="75" fill="#d9e7df"/><path d="M0 60 35 15l30 45 20-25 15 30v10H0Z" fill="#8bb69c"/></svg>');
const labels = ['影像管理', '智能解译', '图斑编辑', '数据管理', '查询搜索', '系统设置', '项目管理'];

async function measureNavigation(page) {
  const profiler = await page.context().newCDPSession(page);
  await profiler.send('Performance.enable');
  await page.bringToFront();
  const before = Object.fromEntries((await profiler.send('Performance.getMetrics')).metrics.map(metric => [metric.name, metric.value]));
  await page.evaluate(() => {
    window.__auditFrames = []; window.__auditLongTasks = []; window.__auditVisibility = [document.visibilityState]; window.__auditRunning = true;
    document.addEventListener('visibilitychange', () => window.__auditVisibility.push(document.visibilityState), { once: true });
    let last;
    const tick = now => { if (last) window.__auditFrames.push(now - last); last = now; if (window.__auditRunning) requestAnimationFrame(tick); };
    requestAnimationFrame(tick);
    new PerformanceObserver(list => window.__auditLongTasks.push(...list.getEntries().map(entry => entry.duration))).observe({ type: 'longtask', buffered: false });
  });
  for (let round = 0; round < 6; round++) {
    for (const label of labels) {
      await page.locator('.nav-list').getByRole('button', { name: label, exact: true }).click();
      await page.waitForTimeout(65);
    }
  }
  const sample = await page.evaluate(() => {
    window.__auditRunning = false;
    const frames = window.__auditFrames.slice(2).sort((a, b) => a - b);
    return { frameCount: frames.length, frameIntervalMedianMs: frames[Math.floor(frames.length * 0.5)], frameIntervalP95Ms: frames[Math.floor(frames.length * 0.95)], frameIntervalMaxMs: frames.at(-1), framesOver50ms: frames.filter(value => value > 50).length, longTasks: window.__auditLongTasks, visibilityStates: window.__auditVisibility };
  });
  const after = Object.fromEntries((await profiler.send('Performance.getMetrics')).metrics.map(metric => [metric.name, metric.value]));
  sample.browserTaskMs = (after.TaskDuration - before.TaskDuration) * 1000;
  sample.layoutMs = (after.LayoutDuration - before.LayoutDuration) * 1000;
  sample.scriptMs = (after.ScriptDuration - before.ScriptDuration) * 1000;
  await profiler.detach();
  return sample;
}

async function main() {
  await fs.mkdir(out, { recursive: true });
  const overview = JSON.parse(await fs.readFile(path.join(fixtureRoot, 'overview-ready.json')));
  const assets = JSON.parse(await fs.readFile(path.join(fixtureRoot, 'assets-mixed.json')));
  const browser = await chromium.launch({ headless: false, executablePath: process.env.AUDIT_BROWSER_EXECUTABLE });
  const capture = process.env.GUI_NAVIGATION_ONLY !== '1';
  const context = await browser.newContext({ reducedMotion: process.env.GUI_REDUCED_MOTION === '1' ? 'reduce' : 'no-preference', viewport: { width: 1440, height: 1000 }, ...(capture ? { recordVideo: { dir: out, size: { width: 1440, height: 1000 } } } : {}) });
  if (capture) await context.tracing.start({ screenshots: true, snapshots: true });
  await context.route('**/visual-audit/tiles/**', route => route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256"><rect width="256" height="256" fill="#e0ebe1"/><path d="M0 0 256 256M256 0 0 256" stroke="#c4d9ca"/><text x="32" y="32" fill="#668774" font-size="12">合成测试底图</text></svg>' }));
  await context.route('**/api/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const endpoint = url.pathname;
    requests.push({ method: request.method(), endpoint });
    let data;
    if (endpoint === '/api/auth/logout') { authenticated = false; data = {}; }
    else if (endpoint === '/api/auth/login') { authenticated = true; data = { authenticated, username: '视觉测试管理员' }; }
    else if (endpoint === '/api/auth/session') data = { authenticated, username: '视觉测试管理员' };
    else if (endpoint === '/api/stats/overview') data = { project_total: 2, project_counts: { active: 1, draft: 1 }, mine_total: 12, feature_total: 168, restored_area_m2: 245000, suspect_mine_count: 2, class_area: { total_area_m2: 1000000, percent: { grassland: 24, forest: 34, building: 8, road: 6, bareground: 20, water: 8 } } };
    else if (endpoint === '/api/projects') data = { items: [overview.data.summary, { ...overview.data.summary, id: 43, name: '滇中矿区监测测试项目' }] };
    else if (endpoint.endsWith('/overview')) data = { ...overview.data, project_id: Number(endpoint.split('/')[3]) };
    else if (endpoint.endsWith('/assets')) data = assets.data;
    else if (endpoint.endsWith('/spatial')) data = { resources: [], jobs: [], map_ready: false };
    else if (endpoint === '/api/projects/42/geojson') data = { type: 'FeatureCollection', features: [{ type: 'Feature', properties: { FID_1: 1, mine_name: '合成测试矿山', SHI: '昆明市', KCFS: '露天开采', status_normalized: 'treated' }, geometry: { type: 'Polygon', coordinates: [[[102.70,25.05],[102.72,25.05],[102.72,25.07],[102.70,25.07],[102.70,25.05]]] } }] };
    else if (endpoint.endsWith('/geojson')) data = { type: 'FeatureCollection', features: [] };
    else if (endpoint === '/api/projects/42/map/manifest') data = { tile_url: `${baseUrl}/visual-audit/tiles/{z}/{x}/{y}.svg`, min_zoom: 0, max_zoom: 15 };
    else if (endpoint.endsWith('/map/manifest')) {
      await route.fulfill({ status: 409, json: { success: false, msg: '测试项目未激活底图' } }); return;
    } else if (endpoint.endsWith('/exports/7/artifact') || endpoint.endsWith('/backups/9/manifest')) {
      await route.fulfill({ contentType: 'application/json', body: '{"fixture":true}' }); return;
    } else if (endpoint.endsWith('/exports')) data = { items: [{ id: 7, format: 'geojson', status: 'completed', artifact_name: 'visual-fixture.geojson', create_time: '2026-10-09T10:00:00Z' }] };
    else if (endpoint.endsWith('/backups')) data = { items: [{ id: 9, snapshot_name: '视觉测试配置快照', status: 'completed', restorable: true, create_time: '2026-10-09T10:00:00Z' }] };
    else if (endpoint.endsWith('/classification-results')) data = { items: [{ result_id: 100 + Number(endpoint.split('/')[3]), mine_fid: 1, year: 2024, vector_status: 'ready', current_revision_no: 0 }] };
    else if (endpoint.includes('kml-roi-history')) {
      await route.fulfill({ json: { success: true, code: 0, count: 21, data: [{ record_id: 'visual-1', type: '地物分类', before_img: thumb, after_img: thumb, data: { fid: 1, year: 2024, file: '视觉测试成果', mode: 'project' } }] } }); return;
    } else if (endpoint.endsWith('/stats')) data = { mineTotal: 1, mineAreaTotal: 12.5, treatedCount: 1, untreatedCount: 0 };
    else if (/\/(timeline|parcel-summary|basemap-candidates|retained|candidates)$/.test(endpoint)) data = { items: [], total: 0 };
    else { missing.push(endpoint); await route.fulfill({ status: 404, json: { success: false, msg: `未提供测试 fixture: ${endpoint}` } }); return; }
    await route.fulfill({ json: { success: true, code: 0, data } });
  });
  const page = await context.newPage();
  await page.bringToFront();
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') consoleErrors.push({ text: message.text(), location: message.location() }); });
  page.on('requestfailed', request => failedRequests.push({ url: request.url(), error: request.failure()?.errorText }));
  const nav = () => page.locator('.nav-list');
  const settle = () => page.evaluate(async () => {
    for (let pass = 0; pass < 2; pass++) {
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      await Promise.all(document.getAnimations().filter(animation => animation.effect?.getTiming().iterations !== Infinity).map(animation => animation.finished.catch(() => {})));
    }
  });
  const screenshot = async name => { await settle(); await page.screenshot({ path: path.join(out, name), fullPage: false }); };
  try {
    await page.goto(`${baseUrl}/#/projects`);
    await page.locator('.overview-panel h2').waitFor();
    if (process.env.GUI_NAVIGATION_ONLY === '1') {
      if (process.env.GUI_DISABLE_BLUR === '1') await page.addStyleTag({ content: '#app * { backdrop-filter: none !important; -webkit-backdrop-filter: none !important; }' });
      await settle();
      const metrics = await measureNavigation(page);
      await fs.writeFile(path.join(out, 'miner-navigation-sample.json'), JSON.stringify({ scope: '同一HTTP fixture与42次导航；仅本机浏览器采样', browser: browser.version(), metrics, errors, missing }, null, 2));
      console.log(JSON.stringify({ metrics, errors, missing, out }, null, 2));
      return;
    }
    assert.equal(await page.locator('.dashboard').count(), 0, '没有项目上下文时不应初始化地图');
    await page.setViewportSize({ width: 1024, height: 900 });
    await settle();
    const buttons = await nav().locator('button').evaluateAll(nodes => nodes.map(node => ({ label: node.getAttribute('aria-label'), icon: !!node.querySelector('svg'), height: node.getBoundingClientRect().height })));
    assert.equal(buttons.length, 7);
    assert.ok(buttons.every(button => button.label && button.icon && button.height >= 40), '折叠导航必须保留可辨认图标、名称与点击区域');
    checks.push('1024px 折叠导航图标、名称和点击区域');
    assert.equal(await page.getByRole('button', { name: '退出登录', exact: true }).locator('svg').count(), 1, '退出图标不依赖系统缺失的符号字形');
    await screenshot('miner-after-1024.png');
    await page.setViewportSize({ width: 1440, height: 1000 });
    await screenshot('miner-after-1440.png');

    const create = page.locator('.workspace-header').getByRole('button', { name: '新建项目', exact: true });
    await create.hover();
    const box = await create.boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.waitForTimeout(130);
    const pressed = await create.evaluate(node => ({ active: node.matches(':active'), hover: node.matches(':hover'), transform: getComputedStyle(node).transform, rect: node.getBoundingClientRect().toJSON(), reduced: matchMedia('(prefers-reduced-motion: reduce)').matches }));
    assert.notEqual(pressed.transform, 'none', '按压应有视觉反馈');
    await page.mouse.up();
    await page.locator('.project-form-panel').waitFor();
    await page.locator('.project-form-panel').getByRole('button', { name: '取消', exact: true }).click();
    checks.push('真实鼠标按压反馈与新建/取消');

    const spatial = page.locator('.spatial-resources-panel');
    await spatial.getByRole('button', { name: '配置空间资源', exact: true }).click();
    await spatial.locator('.resource-reveal').waitFor();
    await screenshot('miner-panel-expanded.png');
    for (let i = 0; i < 6; i++) {
      await spatial.locator('button[aria-expanded]').click();
      await page.waitForTimeout(70);
    }
    await settle();
    assert.equal(await spatial.locator('button[aria-expanded]').getAttribute('aria-expanded'), 'true');
    assert.equal(await spatial.locator('.resource-reveal').count(), 1);
    await spatial.getByRole('button', { name: '收起空间资源', exact: true }).click();
    await page.locator('.resource-reveal').waitFor({ state: 'detached' });
    checks.push('面板连续展开/收起与快速反向切换');

    const exportPanel = page.locator('#project-export');
    for (const [label, filename] of [['下载', 'visual-fixture.geojson'], ['下载清单', 'snapshot-9-manifest.json']]) {
      const pending = page.waitForEvent('download');
      await exportPanel.getByRole('button', { name: label, exact: true }).click();
      const download = await pending;
      assert.equal(download.suggestedFilename(), filename);
      await download.saveAs(path.join(out, filename));
      assert.match(await fs.readFile(path.join(out, filename), 'utf8'), /fixture/);
    }
    checks.push('导出与配置快照按钮产生真实浏览器下载');

    const metrics = await measureNavigation(page);
    assert.equal(await nav().locator('[aria-current="page"]').count(), 1);
    assert.equal(await page.locator('.workspace-page:visible').count(), 1);
    checks.push('42 次连续导航切换：唯一激活项、正确终态、无运行时异常');

    await nav().getByRole('button', { name: '智能解译', exact: true }).click();
    await page.locator('.interpretation-view .record-row').waitFor();
    await page.getByRole('button', { name: '下一页', exact: true }).click();
    await page.locator('.interpretation-view select').selectOption('43');
    await page.locator('.interpretation-view .edit-btn').first().waitFor();
    await screenshot('miner-interpretation.png');
    await page.getByRole('button', { name: '打开地图工作台', exact: true }).click();
    await page.waitForURL('**/#/map/43');
    await page.locator('.map-unavailable').waitFor();
    await screenshot('miner-map-unavailable.png');
    checks.push('历史翻页后切项目仍有成果入口；地图入口携带项目 ID');
    await page.goto(`${baseUrl}/#/map/42`);
    await page.locator('.leaflet-tile-loaded').first().waitFor();
    await page.locator('.leaflet-control-zoom-in').click();
    await page.locator('.map-tool-btn').click();
    for (let i = 0; i < 6; i++) {
      await page.locator('.right-sidebar .collapse-btn').click();
      await page.waitForTimeout(70);
    }
    await screenshot('miner-map-1440.png');
    const mapBox = await page.locator('.map-container').boundingBox();
    for (const selector of ['.map-legend', '.map-tool-btn']) {
      const control = await page.locator(selector).boundingBox();
      assert.ok(control.x >= mapBox.x && control.x + control.width <= mapBox.x + mapBox.width + 1, '地图控件应定位于地图区域');
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await settle();
    assert.ok((await page.locator('.map-container').boundingBox()).width >= 320);
    await page.locator('.map-container').scrollIntoViewIfNeeded();
    await screenshot('miner-map-390.png');
    checks.push('合成瓦片地图真实缩放、经纬网、6次侧栏切换；390px地图可滚动到达且宽度不坍缩');
    await nav().getByRole('button', { name: '项目管理', exact: true }).click();

    for (const width of [390, 768, 1024, 1440]) {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
      await settle();
      const layout = await page.evaluate(() => ({ viewport: innerWidth, pageWidth: document.documentElement.scrollWidth, contentWidth: document.querySelector('.content-area').scrollWidth, contentClient: document.querySelector('.content-area').clientWidth }));
      assert.ok(layout.pageWidth <= layout.viewport + 1 && layout.contentWidth <= layout.contentClient + 1, `项目页面存在横向溢出: ${JSON.stringify(layout)}`);
      await screenshot(`miner-after-${width}.png`);
    }
    checks.push('390/768/1024/1440px 项目页无横向溢出');
    await page.setViewportSize({ width: 1024, height: 480 }); await settle();
    await nav().getByRole('button', { name: '系统设置', exact: true }).click();
    assert.ok(await page.locator('.settings-view').isVisible());
    await screenshot('miner-short-window.png');
    checks.push('480px短窗口末尾导航可滚动点击');

    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.setViewportSize({ width: 1440, height: 1000 });
    for (const label of labels) await nav().getByRole('button', { name: label, exact: true }).click();
    const reduced = await page.locator('.workspace-page').evaluate(node => ({ animation: getComputedStyle(node).animationName, transition: getComputedStyle(node.querySelector('button')).transitionDuration }));
    assert.equal(reduced.animation, 'none'); assert.equal(reduced.transition, '0s');
    await screenshot('miner-reduced-motion.png');
    checks.push('减少动态效果：页面无动画、控件无过渡，导航仍可操作');
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await page.locator('.nav-footer').getByRole('button', { name: '退出', exact: true }).click();
    await page.getByRole('heading', { name: '管理员登录', exact: true }).waitFor();
    await screenshot('miner-login.png');
    await page.getByLabel('账号', { exact: true }).fill('visual-test');
    await page.getByLabel('密码', { exact: true }).fill('synthetic-password');
    await page.getByRole('button', { name: '登录', exact: true }).click();
    await page.locator('.overview-panel h2').waitFor();
    await page.locator('.workspace-header').getByRole('button', { name: '视觉测试管理员 退出登录', exact: true }).click();
    await page.getByRole('heading', { name: '管理员登录', exact: true }).waitFor();
    checks.push('侧栏与工作台退出事件、合成账号登录流程');
    assert.deepEqual(errors, [], '浏览器运行时异常');
    assert.ok(consoleErrors.every(error => !error.text.includes('缺少有效项目 ID')), '隐藏工作台不应产生缺少项目ID的初始化异常');
    assert.deepEqual([...new Set(missing)], [], '存在未经 fixture 覆盖的 API，不能将其算作成功');
    await fs.writeFile(path.join(out, 'miner-gui-results.json'), JSON.stringify({ scope: '可见 Chromium + HTTP fixture；不代表真实 GPU/GIS 验证', browser: browser.version(), checks, metrics, errors, missing, requests, consoleErrors, failedRequests }, null, 2));
    console.log(JSON.stringify({ checks: checks.length, metrics, errors, missing, consoleErrors, failedRequests, out }, null, 2));
  } catch (error) {
    await page.screenshot({ path: path.join(out, 'miner-failure.png') });
    await fs.writeFile(path.join(out, 'miner-gui-failure.json'), JSON.stringify({ checks, error: error.message, errors, missing }, null, 2));
    throw error;
  } finally {
    if (capture) await context.tracing.stop({ path: path.join(out, 'miner-trace.zip') });
    await context.close();
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
