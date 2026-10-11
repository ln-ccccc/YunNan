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
    if (url.port === '9500') {
      const headers = { 'Access-Control-Allow-Origin': 'http://127.0.0.1:4502', 'Access-Control-Allow-Credentials': 'true' };
      if (endpoint === '/api/auth/session') {
        await route.fulfill({ headers, json: { code: 0, success: true, data: { authenticated: true, username: '视觉测试管理员' } } });
      } else if (endpoint.includes('history')) {
        await route.fulfill({ headers, json: { code: 0, success: true, data: [], count: 0 } });
      } else {
        missing.push(endpoint);
        await route.fulfill({ status: 404, headers, json: { success: false, msg: '未提供测试 fixture' } });
      }
      return;
    }
    let data;
    if (endpoint === '/api/auth/logout') { authenticated = false; data = {}; }
    else if (endpoint === '/api/auth/login') { authenticated = true; data = { authenticated, username: '视觉测试管理员' }; }
    else if (endpoint === '/api/auth/session') data = { authenticated, username: '视觉测试管理员' };
    else if (endpoint === '/api/stats/overview') data = { project_total: 2, project_counts: { active: 1, draft: 1 }, mine_total: 12, feature_total: 168, restored_area_m2: 245000, suspect_mine_count: 2, class_area: { total_area_m2: 1000000, percent: { grassland: 24, forest: 34, building: 8, road: 6, bareground: 20, water: 8 } } };
    else if (endpoint === '/api/projects') data = { items: [overview.data.summary, { ...overview.data.summary, id: 43, name: '滇中矿区监测测试项目' }] };
    else if (endpoint.endsWith('/overview')) data = { ...overview.data, project_id: Number(endpoint.split('/')[3]) };
    else if (endpoint.endsWith('/assets')) data = assets.data;
    else if (endpoint.endsWith('/spatial')) data = { resources: [], jobs: [], map_ready: false };
    else if (endpoint === '/api/projects/42/geojson') data = { type: 'FeatureCollection', features: [{ type: 'Feature', properties: { FID_1: 1, mine_name: '合成测试矿山', SHI: '昆明市', KCFS: '露天开采', status_normalized: 'treated', HFZLQK: '已治理', area: 245000 }, geometry: { type: 'Polygon', coordinates: [[[102.70,25.05],[102.72,25.05],[102.72,25.07],[102.70,25.07],[102.70,25.05]]] } }] };
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
    } else if (endpoint.endsWith('/stats')) data = { mineTotal: 1, mineAreaTotal: 245000, treatedCount: 1, untreatedCount: 0, restorationMethodList: [{ name: '生态复绿', count: 1 }], miningMethodList: [{ name: '露天开采', value: 1 }], landTypeList: [{ name: '林地', value: 1 }, { name: '草地', value: 1 }], ndviStats: { mean: 0.462, trend: 0.031, available_mine_count: 1 } };
    else if (endpoint.endsWith('/mines/indices')) data = Object.fromEntries(['ndvi', 'ndbi', 'ndwi', 'ndsi'].map(key => [key, { available: true, mean: 0.462, trend: 0.031, mk_trend: '上升', data: [{ year: 2022, value: 0.41 }, { year: 2023, value: 0.462 }, { year: 2024, value: 0.503 }] }]));
    else if (endpoint.endsWith('/mines/change-matrix')) data = { has_change_matrix: true, data_source: 'project_inference', old_year: 2022, new_year: 2024, headers: ['forest', 'grassland', 'bareground'], matrix: [{ label: 'forest', values: [95, 5, 0] }, { label: 'grassland', values: [40, 60, 0] }, { label: 'bareground', values: [25, 20, 55] }], images: { old: null, new: null } };
    else if (endpoint.endsWith('/mines/original-imagery')) data = { fid: 1, items: [] };
    else if (endpoint.endsWith('/traceability')) data = { fid: 1, years: [] };
    else if (endpoint.endsWith('/mines/trend-report')) data = { mine_total: 1, coverage: { matrix_ready_count: 1, matrix_missing_count: 0 }, class_trends: { selected_class: { upward_count: 0, downward_count: 1, stable_count: 0 } }, tables: { selected_class_rows: [{ fid: 1, mine_name: '合成测试矿山', start_year: 2022, end_year: 2024, start_percent: 60, end_percent: 33, delta_percent: -27, mine_area_m2: 245000 }] } };
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
    assert.match(await page.locator('.stats-panel').innerText(), /0\.245 km²/);
    assert.doesNotMatch(await page.locator('.stats-panel').innerText(), /千m/);

    const create = page.locator('.workspace-header').getByRole('button', { name: '新建项目', exact: true });
    await page.bringToFront();
    await create.hover();
    const box = await create.boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.waitForTimeout(130);
    const pressed = await create.evaluate(node => ({ active: node.matches(':active'), hover: node.matches(':hover'), transform: getComputedStyle(node).transform, rect: node.getBoundingClientRect().toJSON(), reduced: matchMedia('(prefers-reduced-motion: reduce)').matches }));
    assert.notEqual(pressed.transform, 'none', `按压应有视觉反馈: ${JSON.stringify(pressed)}`);
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

    await nav().getByRole('button', { name: '数据管理', exact: true }).click();
    await page.locator('.data-manager-view .export-snapshot-panel').waitFor();
    await screenshot('miner-data-manager.png');

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
    await page.locator('.leaflet-control-zoom-out').click();
    await page.locator('.leaflet-control-zoom-in').click();
    await page.locator('.map-tool-btn').click();
    for (let i = 0; i < 6; i++) {
      await page.locator('.right-sidebar .collapse-btn').click();
      await page.waitForTimeout(70);
    }
    await screenshot('miner-map-1440.png');
    assert.match(await page.locator('.left-sidebar').innerText(), /0\.245 km²/);
    assert.equal(await page.locator('.left-sidebar').evaluate(node => getComputedStyle(node).backgroundColor), 'rgb(248, 251, 250)');
    await page.locator('.search-box input').fill('1');
    await page.locator('.search-box button').click();
    await page.locator('.modal-content .chart-container canvas').waitFor();
    await screenshot('miner-mine-detail.png');
    assert.match(await page.locator('.info-grid').innerText(), /0\.245 km²/);
    const detail = page.locator('.dashboard .modal-content');
    for (const name of ['变化矩阵', '地物分类', '原始影像', 'NDVI', 'NDBI', 'NDWI', 'NDSI']) {
      await detail.getByRole('button', { name, exact: true }).click();
      await page.waitForTimeout(70);
    }
    await detail.getByRole('button', { name: '变化矩阵', exact: true }).click();
    await detail.locator('.matrix-container').waitFor();
    await screenshot('miner-change-matrix.png');
    await page.setViewportSize({ width: 390, height: 844 });
    await screenshot('miner-change-matrix-390.png');
    const modalBox = await detail.boundingBox();
    assert.ok(modalBox.x >= 0 && modalBox.x + modalBox.width <= 390 && modalBox.height <= 844, '移动端详情弹窗应保持在视口内');
    await page.setViewportSize({ width: 1440, height: 1000 });
    await settle();
    await detail.locator('.close-btn').click();
    await page.locator('.dashboard .modal-overlay').waitFor({ state: 'detached' });
    await page.getByRole('button', { name: '趋势统计与导出', exact: false }).click();
    await page.locator('.data-table tbody tr').first().waitFor();
    await screenshot('miner-trend-report.png');
    const report = page.locator('.dashboard .modal-content');
    const reportDownload = page.waitForEvent('download');
    await report.getByRole('button', { name: '导出 CSV', exact: true }).click();
    assert.match((await reportDownload).suggestedFilename(), /trend_report_.*\.csv/);
    await report.locator('.close-btn').click();
    await page.locator('.dashboard .modal-overlay').waitFor({ state: 'detached' });
    checks.push('概览/地图/详情面积正确换算 km²；浅色统计图、7类详情快速切换、变化矩阵、趋势表与 CSV 下载');
    const mapBox = await page.locator('.map-container').boundingBox();
    for (const selector of ['.map-legend', '.map-tool-btn']) {
      const control = await page.locator(selector).boundingBox();
      assert.ok(control.x >= mapBox.x && control.x + control.width <= mapBox.x + mapBox.width + 1, '地图控件应定位于地图区域');
    }
    if (process.env.GUI_PLATFORM_HANDOFF === '1') {
      const handoff = page.locator('.dashboard .system-btn');
      await handoff.hover();
      const handoffBox = await handoff.boundingBox();
      await page.mouse.move(handoffBox.x + handoffBox.width / 2, handoffBox.y + handoffBox.height / 2);
      await page.mouse.down();
      await page.waitForTimeout(130);
      assert.notEqual(await handoff.evaluate(node => getComputedStyle(node).transform), 'none', '地图页解译入口按压反馈应与工作台一致');
      await page.mouse.up();
      await page.waitForURL('http://127.0.0.1:4502/segmentation?project_id=42');
      await page.locator('.tiff-upload-card').waitFor();
      assert.equal(await page.locator('#loader-wrapper').count(), 0);
      await screenshot('miner-to-geoview.png');
      await page.goBack();
      await page.locator('.leaflet-tile-loaded').first().waitFor();
      checks.push('真实点击 Miner 解译平台入口 → GeoView（项目42）→ 浏览器后退回地图');
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
    const reducedCreate = page.locator('.workspace-header').getByRole('button', { name: '新建项目', exact: true });
    await reducedCreate.hover();
    const reducedBox = await reducedCreate.boundingBox();
    await page.mouse.move(reducedBox.x + reducedBox.width / 2, reducedBox.y + reducedBox.height / 2);
    await page.mouse.down();
    const reducedPress = await reducedCreate.evaluate(node => ({ active: node.matches(':active'), transform: getComputedStyle(node).transform }));
    await page.mouse.up();
    await page.locator('.project-form-panel').waitFor();
    await page.locator('.project-form-panel').getByRole('button', { name: '取消', exact: true }).click();
    assert.equal(reducedPress.active, true);
    assert.equal(reducedPress.transform, 'none', '减少动态效果时真实按压不能缩放');
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
