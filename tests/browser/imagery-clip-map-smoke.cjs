// 影像上手绘冒烟（2026-10-24 审查 P1-8）：HTTP fixture 只在测试浏览器内拦截。
// 验证链：选中影像 → 预览 PNG 叠加为 Leaflet 底图 → 地图点击采集真实经纬度顶点 →
// 双击闭合 → 执行裁剪提交的 GeoJSON 落在影像 bounds 内且闭环 → 矿山边界辅助叠加。
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const { chromium } = require(process.env.PLAYWRIGHT_PACKAGE || 'playwright');

const baseUrl = process.env.MINER_UI_URL || 'http://127.0.0.1:4401';
const out = process.env.GUI_AUDIT_OUTPUT || path.join(os.tmpdir(), 'yunnan-imagery-clip-gui');

// 1×1 红色 PNG（内容无关紧要，只验证 imageOverlay 真实挂载）
const RED_PNG_BASE64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';
const BOUNDS = { west: 100.0, south: 25.68, east: 100.48, north: 26.0 };
// 外壳组件的旁路请求（统计看板、项目空间/资产/活动/导出/快照/矿山汇总/底图候选）
const KNOWN_BACKGROUND = /^\/api\/(stats\/overview|projects\/\d+\/(overview|assets|spatial|timeline|exports|backups|mines\/parcel-summary|spatial\/basemaps\/retained))$/;

const checks = [];
const errors = [];
const missing = [];
const consoleErrors = [];
const clipRequests = [];

async function main() {
  await fs.mkdir(out, { recursive: true });
  const browser = await chromium.launch({ headless: false, executablePath: process.env.AUDIT_BROWSER_EXECUTABLE });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') consoleErrors.push({ text: message.text(), location: message.location() }); });

  await context.route('**/api/**', async route => {
    const endpoint = new URL(route.request().url()).pathname;
    const headers = { 'Access-Control-Allow-Origin': baseUrl, 'Access-Control-Allow-Credentials': 'true' };
    let data;
    if (endpoint === '/api/auth/session') {
      data = { authenticated: true, username: '视觉测试管理员' };
    } else if (endpoint === '/api/projects') {
      data = { items: [{ id: 43, name: '影像上手绘测试项目', status: 'active' }] };
    } else if (endpoint.endsWith('/imagery/candidates')) {
      data = { items: [{ dataset_id: null, display_name: '合成影像.tif', storage_key: 'projects/43/inputs/imagery/a.tif', year: null, width: 960, height: 640, count: 3, crs: 'EPSG:4326', size_bytes: 1234567 }], count: 1 };
    } else if (endpoint.endsWith('/imagery/preview')) {
      data = { bounds: BOUNDS, width: 300, height: 200, source_width: 960, source_height: 640, png_base64: RED_PNG_BASE64 };
    } else if (endpoint.endsWith('/geojson')) {
      data = { type: 'FeatureCollection', features: [{ type: 'Feature', properties: { FID_1: 42 }, geometry: { type: 'Polygon', coordinates: [[[100.1, 25.9], [100.2, 25.9], [100.2, 25.98], [100.1, 25.98], [100.1, 25.9]]] } }] };
    } else if (endpoint.endsWith('/imagery/clip')) {
      clipRequests.push(route.request().postDataJSON());
      data = { dataset_id: 11, display_name: '裁剪 合成影像.tif', storage_key: 'projects/43/inputs/imagery/clip_fixture.tif', width: 123, height: 45, buffer_meters: 0 };
    } else if (KNOWN_BACKGROUND.test(endpoint)) {
      // 应用外壳的旁路轮询（统计/项目概览等），与本冒烟的绘制链路无关
      await route.fulfill({ status: 404, headers, json: { success: false, msg: 'background endpoint' } });
      return;
    } else {
      missing.push(endpoint);
      await route.fulfill({ status: 404, headers, json: { success: false, msg: `未提供测试 fixture: ${endpoint}` } });
      return;
    }
    await route.fulfill({ headers, json: { success: true, code: 0, data } });
  });

  try {
    await page.goto(`${baseUrl}/#/imagery`, { waitUntil: 'load' });
    await page.locator('.imagery-view').waitFor();
    await page.locator('#imagery-project').selectOption('43');
    await page.locator('.candidate-row', { hasText: '合成影像.tif' }).click();
    checks.push('候选影像可选并展开工具卡');

    await page.locator('.clip-map.leaflet-container').waitFor();
    const overlay = page.locator('.clip-map img.leaflet-image-layer');
    await overlay.waitFor();
    assert.match(await overlay.getAttribute('src'), /^data:image\/png;base64,/, '底图必须是预览 PNG 数据 URL');
    assert.equal(await page.locator('.draw-svg').count(), 0, '旧 SVG 假想画布不得残留');
    checks.push('预览 PNG 以 Leaflet imageOverlay 挂载为绘制底图');

    // 以预览图像的实际渲染框取点（fitBounds 留白时地图框四角在影像 bounds 之外）
    await page.waitForTimeout(800); // 等 fitBounds 缩放动画结束再取框，避免点击落点偏移
    const box = await overlay.boundingBox();
    const at = (fx, fy) => [box.x + box.width * fx, box.y + box.height * fy];
    const [x1, y1] = at(0.25, 0.3);
    const [x2, y2] = at(0.7, 0.35);
    const [x3, y3] = at(0.55, 0.75);
    await page.mouse.click(x1, y1);
    await page.mouse.click(x2, y2);
    await page.mouse.click(x3, y3);
    // 绘制图层为纯展示（interactive:false，点击穿透），path 无 leaflet-interactive 类
    await page.locator('.clip-map svg.leaflet-zoom-animated path').first().waitFor();
    // Leaflet 圆点标记渲染为 path；顶点数以工具条文案为准
    await page.locator('.draw-ops span').filter({ hasText: '3 个顶点' }).waitFor();
    checks.push('地图点击产生顶点标记与连线');

    await page.mouse.dblclick(x1, y1);
    await page.locator('.draw-ops span').filter({ hasText: '已闭合' }).waitFor();
    checks.push('双击闭合多边形');

    const clipButton = page.getByRole('button', { name: '执行裁剪' });
    await clipButton.click();
    await page.locator('.result-line.ok').filter({ hasText: '裁剪完成' }).waitFor();
    assert.equal(clipRequests.length, 1, '应恰好提交一次裁剪请求');
    const geometry = clipRequests[0].geometry;
    assert.equal(geometry.type, 'Polygon');
    const ring = geometry.coordinates[0];
    assert.equal(ring.length, 6, '3 次单击 + 双击补 1 顶点 + 首点闭合 = 6 个坐标');
    assert.deepEqual(ring[0], ring[ring.length - 1], '环必须首尾闭合');
    for (const [lng, lat] of ring) {
      assert.ok(lng >= BOUNDS.west && lng <= BOUNDS.east, `经度 ${lng} 超出影像范围`);
      assert.ok(lat >= BOUNDS.south && lat <= BOUNDS.north, `纬度 ${lat} 超出影像范围`);
    }
    assert.ok(ring.slice(0, -1).every(([lng, lat]) => lng !== BOUNDS.west || lat !== BOUNDS.north), '顶点不得是常量假想 bbox 角点');
    checks.push('提交的 GeoJSON 顶点全部落在所选影像 bounds 内（真实地理绑定）');

    await page.locator('.tool-card select').first().selectOption('mine');
    await page.locator('.clip-map path[stroke="#e67e22"]').waitFor();
    checks.push('矿山边界来源叠加所选矿山面辅助定位');

    await page.screenshot({ path: path.join(out, 'imagery-clip-map.png'), fullPage: false });
    await fs.writeFile(path.join(out, 'imagery-clip-map-results.json'), JSON.stringify({
      scope: '可见 Chromium + HTTP fixture；不代表真实 GPU/GIS 验证',
      browser: browser.version(), checks, errors, missing, consoleErrors, clipRequests,
    }, null, 2));
    console.log(JSON.stringify({ checks: checks.length, errors, missing, consoleErrors, out }, null, 2));
  } finally {
    await browser.close();
  }
  assert.equal(errors.length, 0, `页面运行时异常: ${errors.join('; ')}`);
  assert.equal(missing.length, 0, `缺少 fixture 的 API: ${missing.join('; ')}`);
}

main().catch(error => { console.error(error); process.exit(1); });
