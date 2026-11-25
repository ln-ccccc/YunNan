// 调试：在最小夹具里画一个面，看编辑完成后是否发出 geometry/validate 请求，并读出组件内部状态。
// 只与本地静态预览通信，全部 /api/ 请求在这里拦截，不访问真实后端。
const { chromium } = require('playwright');

const baseUrl = process.env.GEOVIEW_UI_URL || 'http://127.0.0.1:4403';
const classes = ['草地', '林地', '建筑', '道路', '裸地', '水体'].map((name, index) => ({ class_code: index, class_name_zh: name, class_name: ['grassland', 'forest', 'building', 'road', 'bareground', 'water'][index] }));
const initialFeatureCollection = {
  type: 'FeatureCollection',
  features: [{
    type: 'Feature', id: 'auto-1',
    properties: { class_code: 1, class_name: 'forest', feature_id: 'auto-142-1' },
    geometry: { type: 'Polygon', coordinates: [[[102.70, 25.05], [102.72, 25.05], [102.72, 25.07], [102.70, 25.07], [102.70, 25.05]]] },
  }],
};

(async () => {
  const headed = process.env.AUDIT_HEADFUL === '1';
  const browser = await chromium.launch({ headless: !headed, executablePath: process.env.AUDIT_BROWSER_EXECUTABLE });
  const context = await browser.newContext({ reducedMotion: process.env.DEBUG_REDUCED ? 'reduce' : 'no-preference', viewport: { width: 1440, height: 1000 } });
  const requests = [];
  let validateCalls = 0;
  await context.route('**/api/**', async (route) => {
    const request = route.request();
    const endpoint = new URL(request.url()).pathname;
    const headers = {
      'Access-Control-Allow-Origin': baseUrl, 'Access-Control-Allow-Credentials': 'true',
      'Access-Control-Allow-Headers': 'content-type', 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    };
    if (request.method() === 'OPTIONS') { await route.fulfill({ status: 204, headers }); return; }
    requests.push(`${request.method()} ${endpoint}`);
    let data = {};
    if (endpoint === '/api/auth/session') data = { authenticated: true, username: 'debug' };
    else if (endpoint.endsWith('/classification-results/142')) {
      data = {
        result_id: 142, project_id: 42, mine_fid: 1, year: 2024, vector_status: 'ready',
        current_revision_no: 0, classes, current_feature_collection: initialFeatureCollection,
      };
    } else if (request.method() === 'GET' && endpoint.endsWith('/classification-results/142/revisions')) data = { revisions: [{ revision_no: 1, source: 'manual', feature_count: 2, created_at: '2026-10-09T10:00:00Z' }] };
    else if (endpoint.endsWith('/classification-results/142/geometry/validate')) {
      validateCalls += 1;
      const count = JSON.parse(request.postData() || '{}').geometries.length;
      data = { items: Array.from({ length: count }, () => ({ ok: false, code: process.env.PROBE_CODE || 'OUTSIDE_ROI', field: 'geometry', reason: '几何必须完全位于成果 ROI 内', outside_area_m2: 12.5, outside_distance_m: 3.2 })) };
    } else if (request.method() === 'POST' && endpoint.endsWith('/classification-results/142/revisions')) {
      // 保存响应扣住 1.2 秒再放行，模拟慢响应
      const payload = JSON.parse(request.postData() || '{}');
      console.log('PROBE save request received, holding 1200ms');
      await new Promise((resolve) => setTimeout(resolve, 1200));
      console.log('PROBE save response released');
      data = { feature_collection: payload.feature_collection, current_revision_no: 1 };
    } else { await route.fulfill({ status: 404, headers, json: { success: false, code: 1, msg: 'no fixture' } }); return; }
    await route.fulfill({ headers, json: { code: 0, success: true, data, count: 0 } });
  });
  const page = await context.newPage();
  page.on('pageerror', (error) => console.log('PAGEERR', String(error).slice(0, 240)));
  await page.goto(`${baseUrl}/classification-results/editor?project_id=42&result_id=142`);

async function instrument(page) {
  await page.waitForFunction(() => {
    const container = document.querySelector('#app');
    const app = container && container.__vue_app__;
    const root = app && app._container && app._container._vnode && app._container._vnode.component;
    if (!root) return false;
    const seen = new Set();
    const stack = [root];
    while (stack.length) {
      const inst = stack.pop();
      if (!inst || seen.has(inst)) continue;
      seen.add(inst);
      const ctx = inst.ctx;
      if (ctx && ctx.validationScheduler) {
        const proxy = inst.proxy;
        const log = (name, detail) => console.debug('ZCI', name, JSON.stringify(detail), Math.round(performance.now()));
        const describe = (id) => { try { const feature = proxy.draw.get(id); return feature ? { geometryType: feature.geometry && feature.geometry.type } : null; } catch (error) { return 'error:' + String(error).slice(0, 60); } };
        const original = ctx.validateDrawGeometries;
        ctx.validateDrawGeometries = async function (drawIds) {
          log('validate-start', {
            drawIds, features: (drawIds || []).map(describe), saving: proxy.saving, reloadSeq: proxy.reloadSeq,
            projectId: proxy.context && proxy.context.projectId, resultId: proxy.context && proxy.context.resultId,
            drawIdsOnCanvas: proxy.draw.getAll().features.map((feature) => feature.id), suppress: proxy.suppressDrawEvents,
          });
          const result = await original.call(this, drawIds);
          log('validate-end', { items: result ? result.items.length : null });
          return result;
        };
        proxy.map.on('draw.create', (event) => log('draw.create', { ids: event.features.map((feature) => feature.id) }));
        proxy.map.on('draw.update', (event) => log('draw.update', { ids: (event.features || []).map((feature) => feature.id) }));
        const originalGetAll = proxy.draw.getAll.bind(proxy.draw);
        return true;
      }
      const walk = (vnode) => {
        if (!vnode) return;
        if (vnode.component) stack.push(vnode.component);
        if (Array.isArray(vnode.children)) vnode.children.forEach(walk);
      };
      walk(inst.subTree);
    }
    return false;
  }, null, { timeout: 8000 });
}
  await page.locator('.mapbox-gl-draw_polygon').waitFor();
  page.on('console', (message) => { if (message.text().startsWith('ZCI')) console.log(message.text().slice(0, 900)); });
  await instrument(page);
  await page.waitForTimeout(800);
  await page.locator('.mapbox-gl-draw_polygon').click();
  const box = await page.locator('.classification-map canvas').boundingBox();
  const at = (dx, dy) => [box.x + box.width / 2 + dx, box.y + box.height / 2 + dy];
  for (const [dx, dy] of [[-80, -100], [60, -90], [20, -50]]) await page.mouse.click(...at(dx, dy));
  await page.mouse.dblclick(...at(-30, -60));
  if (process.env.DEBUG_SAVE === '1') {
    const t0 = Date.now();
    await page.getByRole('button', { name: '保存新版本', exact: true }).click();
    console.log('save clicked after draw at +' + (Date.now() - t0) + 'ms');
  }
  const t0 = Date.now();
  const timeline = [];
  for (let tick = 0; tick < 30; tick += 1) {
    await page.waitForTimeout(100);
    const texts = await page.locator('.el-message__content').allInnerTexts();
    if (texts.length) timeline.push(`+${Date.now() - t0}ms ${JSON.stringify(texts)}`);
  }
  console.log('TOAST-TIMELINE', JSON.stringify(timeline));
  if (process.env.DEBUG_SAVE === '1') {
    console.log('timeline', JSON.stringify(requests.map((item, index) => `${index}:${item}`)));
  }
  const state = await page.evaluate(() => {
    let node = document.querySelector('.classification-map');
    while (node && !node.__vue__) node = node.parentElement;
    const vm = node && node.__vue__;
    if (!vm) return 'no vue instance';
    return {
      hasScheduler: Boolean(vm.validationScheduler),
      reloadSeq: vm.reloadSeq, context: vm.context, suppressDrawEvents: vm.suppressDrawEvents,
      drawIds: vm.draw ? vm.draw.getAll().features.map((feature) => feature.id) : null,
      isDirty: vm.isDirty,
    };
  });
  console.log(JSON.stringify({ validateCalls, geometryRequests: requests.filter((item) => item.includes('geometry')), apiRequests: requests.length, state }));
  if (state && state.hasScheduler) {
    const after = await page.evaluate(async (ids) => {
      let node = document.querySelector('.classification-map');
      while (node && !node.__vue__) node = node.parentElement;
      const vm = node.__vue__;
      const before = vm.reloadSeq;
      vm.queueGeometryValidation(ids.map((id) => ({ id })), 'create');
      await new Promise((resolve) => setTimeout(resolve, 600));
      const direct = await vm.validateDrawGeometries(ids);
      return { reloadSeqUnchanged: vm.reloadSeq === before, directResult: direct };
    }, state.drawIds);
    console.log(JSON.stringify({ afterManualQueue: after, validateCallsNow: validateCalls }));
  }
  await browser.close();
})().catch((error) => { console.error('DEBUG FAILED', error); process.exit(1); });
