// 底图缩放越级回归（2026-10-31 审计 P2）：编辑器 raster source 必须应用
// map_manifest 声明的 min_zoom/max_zoom——放大超过金字塔原生最大级别时由
// MapLibre 对最后可用级别过缩放渲染，而不是继续请求不存在的瓦片（404）。
// HTTP fixture 只在测试浏览器内拦截，不写真实项目数据。
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const { chromium } = require(process.env.PLAYWRIGHT_PACKAGE || 'playwright');

const baseUrl = process.env.GEOVIEW_UI_URL || 'http://127.0.0.1:4402';
const out = process.env.GUI_AUDIT_OUTPUT || path.join(os.tmpdir(), 'yunnan-editor-tile-zoom');
const checks = [], errors = [], missing = [], consoleErrors = [], failedRequests = [];
const requestedZooms = [];
const MAX_DECLARED_ZOOM = 15;
const MIN_DECLARED_ZOOM = 8;
const classes = ['草地', '林地', '建筑', '道路', '裸地', '水体'].map((name, index) => ({ class_code: index, class_name_zh: name, class_name: ['grassland','forest','building','road','bareground','water'][index] }));
// 1x1 透明 PNG，作为合成瓦片
const tilePng = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==', 'base64');

async function main() {
  await fs.mkdir(out, { recursive: true });
  const browser = await chromium.launch({ headless: false, executablePath: process.env.AUDIT_BROWSER_EXECUTABLE });
  const context = await browser.newContext({ reducedMotion: 'no-preference', viewport: { width: 1440, height: 1000 } });
  // 单一 route 处理全部 /api/**：Playwright route 按注册逆序匹配，若把瓦片
  // 单独注册在前、api 注册在后，瓦片请求会被 api handler 悬空（不 fulfill），
  // MapLibre 等首批瓦片导致 load 永不触发、控件不出现
  await context.route('**/api/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const endpoint = url.pathname;
    if (endpoint.startsWith('/api/projects/42/map-resources/20/tiles/')) {
      const zoom = Number(endpoint.match(/\/tiles\/(\d+)\//)?.[1]);
      if (Number.isInteger(zoom)) requestedZooms.push(zoom);
      await route.fulfill({ status: 200, contentType: 'image/png', body: tilePng });
      return;
    }
    const headers = { 'Access-Control-Allow-Origin': baseUrl, 'Access-Control-Allow-Credentials': 'true', 'Access-Control-Allow-Headers': 'content-type', 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS' };
    if (request.method() === 'OPTIONS') { await route.fulfill({ status: 204, headers }); return; }
    let data;
    if (endpoint === '/api/auth/session') data = { authenticated: true, username: '视觉测试管理员' };
    else if (endpoint.endsWith('/classification-results/142')) {
      data = {
        result_id: 142, project_id: 42, mine_fid: 1, year: 2024,
        vector_status: 'ready', current_revision_no: 0, classes,
        current_feature_collection: {
          type: 'FeatureCollection',
          features: [{
            type: 'Feature',
            id: 'auto-1',
            properties: { class_code: 1, class_name: 'forest' },
            geometry: { type: 'Polygon', coordinates: [[[102.70, 25.05], [102.72, 25.05], [102.72, 25.07], [102.70, 25.07], [102.70, 25.05]]] },
          }],
        },
        // 声明金字塔范围：后端超范围直接 404（project.py 瓦片路由）
        map_manifest: {
          api_tile_url: '/api/projects/42/map-resources/20/tiles/{z}/{x}/{y}.png',
          bounds: [102.68, 25.03, 102.74, 25.09],
          min_zoom: MIN_DECLARED_ZOOM,
          max_zoom: MAX_DECLARED_ZOOM,
        },
      };
    } else if (request.method() === 'GET' && endpoint.endsWith('/classification-results/142/revisions')) {
      data = { revisions: [{ revision_no: 0, source: 'auto', feature_count: 1, created_at: '2026-10-09T10:00:00Z' }] };
    } else { missing.push(endpoint); await route.fulfill({ status: 404, headers, json: { success: false, code: 1, msg: '未提供测试 fixture' } }); return; }
    await route.fulfill({ headers, json: { code: 0, success: true, data, count: Array.isArray(data) ? data.length : 0 } });
  });
  const page = await context.newPage();
  await page.bringToFront();
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') consoleErrors.push({ text: message.text(), location: message.location() }); });
  page.on('requestfailed', request => failedRequests.push({ url: request.url(), error: request.failure()?.errorText }));
  const settle = () => page.evaluate(async () => {
    for (let pass = 0; pass < 2; pass++) {
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      await Promise.all(document.getAnimations().filter(animation => animation.effect?.getTiming().iterations !== Infinity).map(animation => animation.finished.catch(() => {})));
    }
  });
  try {
    await page.goto(`${baseUrl}/classification-results/editor?project_id=42&result_id=142`);
    await page.locator('.classification-map canvas').waitFor();
    // 当前 maplibre-gl 控件前缀为 maplibregl-（MapboxDraw 自身仍用 mapbox-gl-draw_）
    await page.locator('.maplibregl-ctrl-zoom-in').waitFor();
    await page.waitForTimeout(600); // 等首屏瓦片请求发出
    const zoomIn = page.locator('.maplibregl-ctrl-zoom-in');
    for (let i = 0; i < 6; i++) {
      await zoomIn.click();
      await page.waitForTimeout(120);
    }
    await settle();
    await page.waitForTimeout(400);

    assert.ok(requestedZooms.length > 0, 'fixture 瓦片应被真实请求（否则测试无效）');
    const overzoom = requestedZooms.filter(zoom => zoom > MAX_DECLARED_ZOOM);
    const underzoom = requestedZooms.filter(zoom => zoom < MIN_DECLARED_ZOOM);
    assert.deepEqual(overzoom, [], `放大超过声明最大级别时不得请求不存在的瓦片（z>${MAX_DECLARED_ZOOM}），实际请求级别: ${[...new Set(requestedZooms)].sort((a, b) => a - b).join(',')}`);
    assert.deepEqual(underzoom, [], `不得请求低于声明最小级别的瓦片（z<${MIN_DECLARED_ZOOM}）`);
    assert.deepEqual(failedRequests, [], '不得存在失败请求（未修复时越级瓦片 404 会落在这里）');
    checks.push(`编辑器 raster source 应用 manifest min_zoom=${MIN_DECLARED_ZOOM}/max_zoom=${MAX_DECLARED_ZOOM}；6 次放大后实际请求级别 ${[...new Set(requestedZooms)].sort((a, b) => a - b).join(',')}，无越级请求与失败请求`);

    assert.deepEqual(errors, [], '浏览器运行时异常');
    assert.deepEqual([...new Set(missing)], [], '未经 fixture 覆盖的 API');
    await fs.writeFile(path.join(out, 'tile-zoom-results.json'), JSON.stringify({
      scope: '可见Chromium+HTTP fixture；验证前端瓦片请求行为，不代表真实金字塔/GPU',
      browser: browser.version(), checks, requestedZooms: [...new Set(requestedZooms)].sort((a, b) => a - b), errors, missing, consoleErrors, failedRequests,
    }, null, 2));
    console.log(JSON.stringify({ checks: checks.length, requestedZooms: [...new Set(requestedZooms)].sort((a, b) => a - b), errors, missing, consoleErrors, failedRequests, out }, null, 2));
  } catch (error) {
    await page.screenshot({ path: path.join(out, 'tile-zoom-failure.png') });
    await fs.writeFile(path.join(out, 'tile-zoom-failure.json'), JSON.stringify({ checks, error: error.message, errors, missing, requestedZooms }, null, 2));
    throw error;
  } finally {
    await context.close();
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
