// 分类矢量编辑器 —— 编辑期校验与合并 / 简化异常处理的真实浏览器回归（2026-11-25）。
//
// 为什么需要真实浏览器：校验的「状态 + 提示」逻辑是纯函数（utils/classificationEditorValidation.mjs，
// 有 node 行为测试），但它接在绘制库上——draw.create / draw.update 什么时候发、撤销重做的 draw.set()
// 不发事件、请求在途时画布还能不能动、Element Plus 的消息合并选项在真实组件里是否成立——这些只有
// 加载真实地图与真实消息组件才能验证。2026-11-24 的第一版把请求失败静默吞掉，而当时数「请求发出去了」
// 的夹具和源码守卫式的单测全是绿的。
//
// 每个场景断言的都是看得见的结果：提示文案、工具区的校验状态条、画布上的几何、撤销栈能不能回到原样。
// HTTP fixture 只在测试浏览器内拦截：不登录真实账号、不写真实项目、不访问真实后端，也不实现任何几何算法
// （validate 按脚本指定的结论回答，union / simplify 返回预先写好的固定几何）。
//
// 从仓库根目录运行（路径换成自己的）：
//   $env:PLAYWRIGHT_PACKAGE='.../node_modules/playwright-core'
//   $env:AUDIT_BROWSER_EXECUTABLE='.../chrome.exe'
//   $env:GEOVIEW_UI_URL='http://127.0.0.1:4403'
//   $env:GUI_AUDIT_OUTPUT='.../out/browser-validation'
//   node tests/browser/geoview-editor-validation-smoke.cjs
// 默认无头 + 真 GPU（SwiftShader 软渲染可能不出帧，见 testing-playbook 技巧 30）；设 AUDIT_HEADFUL=1 用可见浏览器。
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_PACKAGE || 'playwright');

const baseUrl = process.env.GEOVIEW_UI_URL || 'http://127.0.0.1:4403';
const out = process.env.GUI_AUDIT_OUTPUT || path.join(os.tmpdir(), 'yunnan-editor-validation');
const PROJECT_ID = 42;
const RESULT_ID = 142;
const OTHER_RESULT_ID = 143;
const editorUrl = (resultId) => `${baseUrl}/classification-results/editor?project_id=${PROJECT_ID}&result_id=${resultId}`;
const classes = ['草地', '林地', '建筑', '道路', '裸地', '水体'].map((name, index) => ({
  class_code: index, class_name_zh: name,
  class_name: ['grassland', 'forest', 'building', 'road', 'bareground', 'water'][index],
}));
const forest = {
  type: 'Feature',
  properties: { class_code: 1, class_name: 'forest', feature_id: 'auto-142-1' },
  geometry: { type: 'Polygon', coordinates: [[[102.70, 25.05], [102.72, 25.05], [102.72, 25.07], [102.70, 25.07], [102.70, 25.05]]] },
};
const roiGeometry = { type: 'Polygon', coordinates: [[[102.68, 25.03], [102.74, 25.03], [102.74, 25.09], [102.68, 25.09], [102.68, 25.03]]] };
const OUTSIDE = { ok: false, code: 'OUTSIDE_ROI', field: 'geometry', reason: '几何必须完全位于成果 ROI 内', outside_area_m2: 3.2, outside_distance_m: 1.1 };
const OK = { ok: true };
// union / simplify 的固定应答（不由输入算出）。位置按初始视图换算：夹具把林地大图斑（102.70–102.72°E、
// 25.05–25.07°N）铺满画布、四边各留 64px；简化后的面放在画布右侧偏中（约 fx 0.60–0.72、fy 0.36–0.42），
// 与其它场景里按屏幕比例画的面错开，顶点都在画布内
const UNION_GEOMETRY = { type: 'Polygon', coordinates: [[[102.7095, 25.0582], [102.7120, 25.0582], [102.7120, 25.0590], [102.7095, 25.0590], [102.7095, 25.0582]]] };
const SIMPLIFIED_GEOMETRY = { type: 'Polygon', coordinates: [[[102.7132, 25.0635], [102.7163, 25.0635], [102.7163, 25.0620], [102.7148, 25.0624], [102.7132, 25.0620], [102.7132, 25.0635]]] };
const SIMPLIFIED_CENTROID = [102.7148, 25.0627];
const SIMPLIFY_OUTCOMES = {
  changed: { outcome: 'changed', geometry: SIMPLIFIED_GEOMETRY, tolerance: 2e-6, area_change: 0.0031, shape_change: 0.018 },
  unchanged: { outcome: 'unchanged' },
  drift: { outcome: 'rejected', reason: { ok: false, code: 'SHAPE_DRIFT', field: 'geometry', reason: '简化后的面积或形态变化会超过精度上限' } },
  outside: { outcome: 'rejected', reason: { ok: false, code: 'OUTSIDE_ROI', field: 'geometry', reason: '几何必须完全位于成果 ROI 内' } },
};

function bodyOf(request) {
  try {
    return JSON.parse(request.postData() || '{}');
  } catch (_) {
    return null;
  }
}

/** 夹具后端。每个浏览器上下文一份，互不干扰。 */
const createBackend = () => ({
  // validate：默认全部合法。plan 是逐次请求的安排（每次请求取走一项，取完回到默认）：
  //   { mode: 'ok' | 'verdict' | 'network' | 'http500' | 'http404' | 'malformed' | 'hold', verdict? }
  validate: { plan: [] },
  // simplify / union：mode 为 ok | http500 | malformed | hold
  simplify: { mode: 'ok', outcome: 'changed' },
  union: { mode: 'ok' },
  save: 'ok', // ok | 422
});

async function installFixture(context, backend, requests) {
  const held = { validate: [], simplify: [], union: [] };
  // 扣住一个请求：放行之后才把应答发回去
  const hold = (kind, respond) => new Promise((resolve) => { held[kind].push({ respond, resolve }); });
  const flushHeld = async () => {
    for (const kind of Object.keys(held)) {
      for (const pending of held[kind].splice(0)) {
        pending.resolve();
        await pending.respond().catch(() => {});
      }
    }
  };
  await context.route('**/api/**', async (route) => {
    const request = route.request();
    const endpoint = new URL(request.url()).pathname;
    const headers = {
      'Access-Control-Allow-Origin': baseUrl, 'Access-Control-Allow-Credentials': 'true',
      'Access-Control-Allow-Headers': 'content-type', 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    };
    const fail = async (status, msg, details) => route.fulfill({ status, headers, json: { success: false, code: 1, msg, ...(details ? { data: { details } } : {}) } });
    if (request.method() === 'OPTIONS') { await route.fulfill({ status: 204, headers }); return; }
    let data;
    if (endpoint === '/api/auth/session') {
      data = { authenticated: true, username: '视觉测试管理员' };
    } else if (endpoint.endsWith(`/classification-results/${RESULT_ID}`) || endpoint.endsWith(`/classification-results/${OTHER_RESULT_ID}`)) {
      const id = Number(endpoint.split('/').pop());
      data = {
        result_id: id, project_id: PROJECT_ID, mine_fid: id, year: 2024, vector_status: 'ready',
        current_revision_no: 0, classes, current_feature_collection: { type: 'FeatureCollection', features: [forest] },
        roi_geometry: roiGeometry, map_manifest: { project_id: PROJECT_ID, bounds: [102.68, 25.03, 102.74, 25.09], api_tile_url: null },
      };
    } else if (request.method() === 'GET' && endpoint.endsWith('/revisions')) {
      data = { current_revision_no: 0, revisions: [{ revision_no: 0, source: 'auto', feature_count: 1, created_at: '2026-10-09T10:00:00Z' }] };
    } else if (request.method() === 'POST' && endpoint.endsWith('/geometry/validate')) {
      const body = bodyOf(request);
      requests.validate.push(body);
      const step = backend.validate.plan.length ? backend.validate.plan.shift() : backend.validate;
      const { mode } = step;
      const verdict = step.verdict || OK;
      if (mode === 'network') { await route.abort('connectionfailed'); return; }
      if (mode === 'http500') { await fail(500, '分类成果服务暂时不可用'); return; }
      if (mode === 'http404') { await fail(404, 'Not Found'); return; }
      if (mode === 'malformed') {
        // 被代理改写或截断过的响应：结论比送审的面少一个
        await route.fulfill({ headers, json: { code: 0, success: true, data: { items: (body?.geometries || []).slice(1).map(() => OK) } } });
        return;
      }
      const respond = () => route.fulfill({
        headers, json: { code: 0, success: true, data: { items: (body?.geometries || []).map(() => verdict) } },
      });
      if (mode === 'hold') { await hold('validate', respond); await respond().catch(() => {}); return; }
      await respond();
      return;
    } else if (request.method() === 'POST' && endpoint.endsWith('/geometry/simplify')) {
      const body = bodyOf(request);
      requests.simplify.push(body);
      const { mode } = backend.simplify;
      if (mode === 'http500') { await fail(500, '分类成果服务暂时不可用'); return; }
      if (mode === 'malformed') {
        // 被代理改写或截断过的响应：送审 1 个面却回来 2 项。数量对不上，前端必须整次按失败处理、一个面都不动
        await route.fulfill({ headers, json: { code: 0, success: true, data: { mode: 'adaptive', tolerance: null, pixel_size_m: 0.24, items: [{ outcome: 'changed', geometry: SIMPLIFIED_GEOMETRY }, { outcome: 'unchanged' }] } } });
        return;
      }
      const respond = () => route.fulfill({
        headers,
        json: { code: 0, success: true, data: { mode: 'adaptive', tolerance: null, pixel_size_m: 0.24, items: (body?.geometries || []).map(() => SIMPLIFY_OUTCOMES[backend.simplify.outcome]) } },
      });
      if (mode === 'hold') { await hold('simplify', respond); await respond().catch(() => {}); return; }
      await respond();
      return;
    } else if (request.method() === 'POST' && endpoint.endsWith('/geometry/union')) {
      const body = bodyOf(request);
      requests.union.push(body);
      if (backend.union.mode === 'http500') { await fail(500, '分类成果服务暂时不可用'); return; }
      if (backend.union.mode === 'malformed') {
        await route.fulfill({ headers, json: { code: 0, success: true, data: { geometry: { type: 'Polygon' } } } });
        return;
      }
      const respond = () => route.fulfill({ headers, json: { code: 0, success: true, data: { geometry: UNION_GEOMETRY } } });
      if (backend.union.mode === 'hold') { await hold('union', respond); await respond().catch(() => {}); return; }
      await respond();
      return;
    } else if (request.method() === 'POST' && endpoint.endsWith('/revisions')) {
      const payload = bodyOf(request);
      requests.save.push(payload);
      if (backend.save === '422') {
        await fail(422, '分类成果保存数据不合法', { field: 'geometry', reason: '几何必须完全位于成果 ROI 内', feature_index: 1 });
        return;
      }
      // 模拟服务端规范化：既有 ID 保留，client-* 分配正式 ID
      let sequence = 0;
      const normalized = {
        type: 'FeatureCollection',
        features: (payload?.feature_collection?.features || []).map((feature) => {
          const id = feature.properties?.feature_id;
          return {
            type: 'Feature',
            properties: {
              feature_id: typeof id === 'string' && id && !id.startsWith('client-') ? id : `manual-${RESULT_ID}-${sequence += 1}`,
              class_code: feature.properties?.class_code,
            },
            geometry: feature.geometry,
          };
        }),
      };
      await route.fulfill({ headers, json: { code: 0, success: true, data: { feature_collection: normalized, current_revision_no: 1 } } });
      return;
    } else { await route.fulfill({ status: 404, headers, json: { success: false, code: 1, msg: '未提供测试 fixture' } }); return; }
    await route.fulfill({ headers, json: { code: 0, success: true, data } });
  });
  return { held, hold, flushHeld };
}

/** 在页面上做编辑动作用的一组工具：读编辑器实例、点地图、画面、拖顶点、看提示与状态条 */
async function createTools(page, box) {
  const editor = (body, arg) => page.evaluate(({ body, arg }) => {
    const seen = new Set();
    let found = null;
    const visit = (vnode) => {
      if (!vnode || found || typeof vnode !== 'object' || seen.has(vnode)) return;
      seen.add(vnode);
      if (vnode.component) {
        const state = vnode.component.proxy || {};
        if (state.map && typeof state.map.project === 'function' && state.draw && typeof state.draw.getAll === 'function') { found = state; return; }
        visit(vnode.component.subTree);
        if (found) return;
      }
      if (Array.isArray(vnode.children)) vnode.children.forEach(visit);
      if (vnode.suspense) visit(vnode.suspense.activeBranch);
    };
    visit(document.querySelector('#app')._vnode);
    if (!found) throw new Error('没找到编辑器实例');
    return new Function('state', 'arg', body)(found, arg);
  }, { body, arg });
  // 点击地图前先确认落点是画布（提示浮层、控件会吞掉落在它们下面的点击，页面不报错）
  const clickMap = async (point) => {
    const hit = await page.evaluate(({ x, y }) => {
      const element = document.elementFromPoint(x, y);
      return element ? `${element.tagName}.${String(element.className).split(' ')[0]}` : '空';
    }, point);
    assert.equal(hit, 'CANVAS.maplibregl-canvas', `地图点击 (${Math.round(point.x)}, ${Math.round(point.y)}) 落在 ${hit} 上，不是画布`);
    await page.mouse.move(point.x - 3, point.y - 2, { steps: 3 });
    await page.mouse.move(point.x, point.y, { steps: 2 });
    await page.mouse.down(); await page.mouse.up();
    await page.waitForTimeout(250);
  };
  // 初始视图（夹具把林地大图斑铺满画布、四边各留 64px）。保存 422 时编辑器会把地图放大定位到出错的面，
  // 之后按固定经纬度取屏幕坐标之前要先把视图复位
  const initialView = await editor('return { center: state.map.getCenter(), zoom: state.map.getZoom() };');
  const resetView = async () => {
    await editor('state.map.jumpTo({ center: arg.center, zoom: arg.zoom });', initialView);
    await page.waitForTimeout(800);
  };
  const at = (fx, fy) => ({ x: box.x + box.width * fx, y: box.y + box.height * fy });
  const quadAt = (fx, fy, w, h) => [at(fx, fy), at(fx + w, fy), at(fx + w, fy + h), at(fx, fy + h)];
  const centroid = (points) => ({
    x: points.reduce((sum, point) => sum + point.x, 0) / points.length,
    y: points.reduce((sum, point) => sum + point.y, 0) / points.length,
  });
  const toScreen = async ([lng, lat]) => editor([
    "const rect = document.querySelector('.classification-map canvas.maplibregl-canvas').getBoundingClientRect();",
    'const point = state.map.project(arg);',
    'return { x: rect.x + point.x, y: rect.y + point.y };',
  ].join('\n'), [lng, lat]);
  const focusCanvas = async () => { await page.locator('.classification-map canvas.maplibregl-canvas').focus(); };
  const drawPolygon = async (points) => {
    await page.click('.mapbox-gl-draw_polygon');
    for (const point of points) await clickMap(point);
    await focusCanvas();
    await page.keyboard.press('Enter');
    await page.waitForTimeout(450);
  };
  const undo = async () => {
    await focusCanvas();
    await page.keyboard.down('Control'); await page.keyboard.press('z'); await page.keyboard.up('Control');
    await page.waitForTimeout(350);
  };
  const redo = async () => {
    await focusCanvas();
    await page.keyboard.down('Control'); await page.keyboard.press('y'); await page.keyboard.up('Control');
    await page.waitForTimeout(350);
  };
  // 最近一个面的各顶点屏幕坐标
  const lastFeatureVertices = () => editor([
    "const rect = document.querySelector('.classification-map canvas.maplibregl-canvas').getBoundingClientRect();",
    'const features = state.draw.getAll().features;',
    'const ring = features[features.length - 1].geometry.coordinates[0];',
    'const toScreen = (lngLat) => { const point = state.map.project(lngLat); return { x: rect.x + point.x, y: rect.y + point.y }; };',
    'return ring.slice(0, -1).map(toScreen);',
  ].join('\n'));
  // 清掉选择：在顶点模式下 Esc 退回普通选择，在普通选择下 Esc 清空选中；两下之后一定是「什么都没选」
  const clearSelection = async () => {
    await focusCanvas();
    await page.keyboard.press('Escape');
    await page.waitForTimeout(200);
    await page.keyboard.press('Escape');
    await page.waitForTimeout(200);
  };
  // 选中一个面。刚画完的面本来就处于选中态，直接再点它会进入顶点模式，所以先清掉选择
  const selectFace = async (point) => {
    await clearSelection();
    await clickMap(point);
    await page.locator('.selection-count').waitFor();
  };
  // 进入顶点模式：先选中，再点一次同一个面
  const enterVertexMode = async (point) => {
    await selectFace(point);
    await clickMap(point);
    await page.waitForTimeout(350);
  };
  // 拖动最近一个面的某个顶点（真实鼠标事件；draw.update 在松开鼠标时发出）。
  // 位移一律取 18 像素以上：实测（新旧构建相同）顶点拖动有约 12 像素的死区——位移在 (10, 6) 像素以内
  // 不被识别，面原地不动并退出顶点模式；(12, 7) 以上才生效。这是绘制库 + 本项目顶点模式的既有行为，
  // 本夹具不测它，也不能被它误导
  const dragLastVertex = async (index, dx, dy) => {
    const vertex = (await lastFeatureVertices())[index];
    await clickMap(vertex);
    await page.mouse.move(vertex.x, vertex.y);
    await page.mouse.down();
    await page.mouse.move(vertex.x + 2 * Math.sign(dx), vertex.y + 1 * Math.sign(dy));
    await page.mouse.move(vertex.x + dx, vertex.y + dy, { steps: 10 });
    await page.mouse.up();
    await page.waitForTimeout(500);
  };
  // 看得见的结果
  const toasts = async () => (await page.locator('.el-message__content').allInnerTexts()).map((text) => text.trim());
  const waitToast = (source, timeout = 6000) => page.waitForFunction(
    (pattern) => [...document.querySelectorAll('.el-message__content')].some((node) => new RegExp(pattern).test(node.textContent)),
    source.source, { timeout },
  );
  const waitToastsCleared = () => page.waitForFunction(() => !document.querySelector('.el-message'), null, { timeout: 15000 });
  const chip = async () => {
    const node = page.locator('[data-testid="validation-state"]');
    if (!(await node.count())) return null;
    return { text: (await node.innerText()).trim(), cls: await node.getAttribute('class'), title: await node.getAttribute('title') };
  };
  const waitChip = (source, timeout = 6000) => page.waitForFunction(
    (pattern) => {
      const node = document.querySelector('[data-testid="validation-state"]');
      return node && new RegExp(pattern).test(node.textContent);
    }, source.source, { timeout },
  );
  const waitChipGone = () => page.waitForFunction(() => !document.querySelector('[data-testid="validation-state"]'), null, { timeout: 8000 });
  const legalNotices = async () => (await toasts()).filter((text) => /超出成果 ROI|几何无效|未能确认/.test(text));
  // 画布与撤销栈的现状（用于「失败后保持原样」的逐值比较）
  const canvas = () => editor('return state.draw.getAll().features.map((f) => ({ geometry: f.geometry, class_code: f.properties.class_code }));');
  const historyFlags = () => editor('return [state.canUndo, state.canRedo];');
  const featureCount = () => editor('return state.draw.getAll().features.length;');
  const trackedStatuses = () => editor('return state.draw.getAll().features.map((f) => [f.properties.feature_id, state.validationCoordinator.statusOf(f.id)]);');
  const toolbarButton = (text) => page.locator('.editor-toolbar button', { hasText: text });
  // 等工具栏按钮结束加载。不要求它可用：合并 / 简化成功后选择会被清空，按钮按设计禁用
  const waitButtonIdle = (text) => page.waitForFunction((label) => {
    const button = [...document.querySelectorAll('.editor-toolbar button')].find((node) => node.textContent.includes(label));
    return Boolean(button) && !button.classList.contains('is-loading');
  }, text, { timeout: 10000 });
  const clickTool = async (text) => {
    await toolbarButton(text).click();
    await page.waitForTimeout(250);
  };
  return {
    editor, at, quadAt, centroid, toScreen, resetView, drawPolygon, undo, redo, clearSelection, selectFace, enterVertexMode, dragLastVertex, clickMap,
    toasts, waitToast, waitToastsCleared, chip, waitChip, waitChipGone, legalNotices,
    canvas, historyFlags, featureCount, trackedStatuses, toolbarButton, waitButtonIdle, clickTool,
  };
}

async function main() {
  await fs.mkdir(out, { recursive: true });
  const checks = [];
  const pageErrors = [];
  const consoleErrors = [];
  const browser = await chromium.launch({
    headless: process.env.AUDIT_HEADFUL !== '1',
    executablePath: process.env.AUDIT_BROWSER_EXECUTABLE || undefined,
    args: ['--use-gl=angle', '--use-angle=d3d11', '--ignore-gpu-blocklist'],
  });

  // ── 请求超时（15 秒）：单独一个浏览器上下文，与下面的主流程并行，不拖长总时长 ──
  const timeoutRun = (async () => {
    const timeoutBackend = createBackend();
    const timeoutRequests = { validate: [], simplify: [], union: [], save: [] };
    const timeoutContext = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const timeoutFixture = await installFixture(timeoutContext, timeoutBackend, timeoutRequests);
    const timeoutPage = await timeoutContext.newPage();
    timeoutPage.on('pageerror', (error) => pageErrors.push(String(error.stack || error).slice(0, 500)));
    await timeoutPage.goto(editorUrl(RESULT_ID), { waitUntil: 'domcontentloaded' });
    await timeoutPage.locator('.classification-map canvas.maplibregl-canvas').waitFor({ timeout: 30000 });
    await timeoutPage.waitForTimeout(1200);
    const timeoutBox = await timeoutPage.locator('.classification-map canvas.maplibregl-canvas').boundingBox();
    const timeoutTools = await createTools(timeoutPage, timeoutBox);
    // 请求被一直扣住不回：15 秒后 axios 放弃
    timeoutBackend.validate.plan.push({ mode: 'hold' });
    const started = Date.now();
    await timeoutTools.drawPolygon(timeoutTools.quadAt(0.08, 0.10, 0.06, 0.06));
    await timeoutPage.waitForFunction(() => /未确认/.test(document.querySelector('[data-testid="validation-state"]')?.textContent || ''), null, { timeout: 26000 });
    const elapsed = Date.now() - started;
    const texts = await timeoutTools.toasts();
    const chip = await timeoutTools.chip();
    await timeoutPage.screenshot({ path: path.join(out, '90-timeout.png') });
    await timeoutFixture.flushHeld();
    await timeoutContext.close().catch(() => {});
    return { elapsed, texts, chip };
  })();

  // 主流程若先失败并关掉浏览器，这个任务会被打断：接住它的拒绝，别盖住真正的失败原因
  const timeoutRunSafe = timeoutRun.catch((error) => ({ interrupted: String(error) }));

  const backend = createBackend();
  const requests = { validate: [], simplify: [], union: [], save: [] };
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const fixture = await installFixture(context, backend, requests);
  const page = await context.newPage();
  // 任何未捕获异常都算失败
  page.on('pageerror', (error) => pageErrors.push(String(error.stack || error).slice(0, 500)));
  page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(message.text().slice(0, 300)); });
  const shot = async (name) => page.screenshot({ path: path.join(out, `${name}.png`) });
  // 放行最早一个被扣住的响应
  const release = async (kind) => {
    assert.ok(fixture.held[kind].length, `没有挂起的 ${kind} 响应可放行`);
    await fixture.held[kind].shift().resolve();
    await page.waitForTimeout(450);
  };
  let t = null;
  try {
    await page.goto(editorUrl(RESULT_ID), { waitUntil: 'domcontentloaded' });
    await page.locator('.classification-map canvas.maplibregl-canvas').waitFor({ timeout: 30000 });
    await page.waitForTimeout(1500);
    const box = await page.locator('.classification-map canvas.maplibregl-canvas').boundingBox();
    t = await createTools(page, box);
    const faceA = t.quadAt(0.08, 0.10, 0.06, 0.06);          // 校验类场景用的面
    const faceAInside = t.at(0.11, 0.13);
    const CONFIRM_DISCARD = '丢弃修改并继续';

    assert.equal(await t.featureCount(), 1, '夹具初始只有一个面');
    assert.equal(await t.chip(), null, '没有编辑过：不显示校验状态');

    // ── 1. 越界结论：提示 + 状态条；删掉这个面之后状态条清掉 ──
    backend.validate.plan.push({ mode: 'verdict', verdict: OUTSIDE });
    await t.drawPolygon(faceA);
    await t.waitToast(/1 个面超出成果 ROI（矿山边界），保存时将被拒绝，请拖回界内或删除/);
    await t.waitChip(/1 个面未通过校验/);
    assert.match((await t.chip()).cls, /\bis-invalid\b/, '未通过：红色');
    assert.match((await t.chip()).title, /保存时会被拒绝/, '悬停说明');
    const statuses = await t.trackedStatuses();
    assert.deepEqual(statuses.map(([, status]) => status).sort(), ['invalid', null].sort(), '协调器里：新面是 invalid，没动过的面不在跟踪中');
    await shot('01-outside-verdict');
    await t.selectFace(faceAInside);
    await page.click('.mapbox-gl-draw_trash');
    await t.waitChipGone();
    assert.equal(await t.featureCount(), 1, '越界的面已删除');
    checks.push('后端回越界：提示与状态条同时出现；删掉这个面后状态条清掉');
    await t.undo();
    await t.waitToastsCleared();

    // ── 2. 校验中：请求未回来之前是「校验中」，回来是合法就消失 ──
    backend.validate.plan.push({ mode: 'hold' });
    await t.drawPolygon(faceA);
    await t.waitChip(/校验中 1 个面/);
    assert.match((await t.chip()).cls, /\bis-pending\b/, '校验中：灰色');
    assert.deepEqual(await t.legalNotices(), [], '校验中不弹任何合法性提示');
    await shot('02-pending');
    await release('validate');
    await t.waitChipGone();
    checks.push('请求在途时状态条显示「校验中」，合法结论回来后消失');
    await t.undo();

    // ── 3. 断网：未确认 + 说明；下一次校验成功就恢复 ──
    backend.validate.plan.push({ mode: 'network' });
    await t.drawPolygon(faceA);
    await t.waitToast(/1 个面的合法性未能确认（网络连接失败）；可以继续编辑，保存时以后端校验为准/);
    await t.waitChip(/1 个面未确认/);
    assert.match((await t.chip()).cls, /\bis-unknown\b/, '未确认：橙色');
    assert.match((await t.chip()).title, /尚未确认.*保存时以后端校验为准/);
    assert.deepEqual((await t.toasts()).filter((text) => /超出成果 ROI|几何无效/.test(text)), [], '未确认不冒充越界或无效');
    await shot('03-network-failure');
    // 「未确认」不妨碍继续编辑：拖一个顶点，新一次校验成功
    const validateRequestsBefore = requests.validate.length;
    await t.enterVertexMode(faceAInside);
    await t.dragLastVertex(0, -14, -10);
    assert.equal(requests.validate.length, validateRequestsBefore + 1, '拖动顶点之后应再发一次校验请求');
    await t.waitChipGone();
    await t.waitToastsCleared();
    checks.push('断网：状态条与提示说明「未确认」；继续编辑后校验成功即恢复');
    await t.undo();
    await t.undo();
    await t.waitChipGone();

    // ── 4. HTTP 500 / 404 / 响应格式异常：各自有具体原因，都不是「合法」 ──
    for (const [mode, reason, name] of [
      ['http500', '校验服务出错', '04-http-500'],
      ['http404', '校验接口不存在，后端可能还没有更新到配套版本', '05-http-404'],
      ['malformed', '服务返回的数据与送审的面对不上', '06-malformed'],
    ]) {
      backend.validate.plan.push({ mode });
      await t.drawPolygon(faceA);
      await t.waitToast(new RegExp(`1 个面的合法性未能确认（${reason}）；可以继续编辑，保存时以后端校验为准`));
      await t.waitChip(/1 个面未确认/);
      assert.deepEqual((await t.toasts()).filter((text) => /超出成果 ROI|几何无效/.test(text)), [], `${name}：不得弹出下标错位或凭空的越界提示`);
      await shot(name);
      await t.undo();
      await t.waitChipGone();
      await t.waitToastsCleared();
    }
    checks.push('HTTP 500 / 404 / 响应格式异常：状态条为「未确认」，提示说明具体原因，不弹错位的越界提示');

    // ── 5. 快速连续编辑：前一次几何的结论晚到，是过期结论，不提示 ──
    backend.validate.plan.push({ mode: 'hold', verdict: OUTSIDE }, { mode: 'ok' });
    await t.drawPolygon(faceA);                       // 第一次送审（旧几何）：结论越界，但被扣住
    await t.enterVertexMode(faceAInside);
    await t.dragLastVertex(0, -16, -12);              // 同一个面又改了：第二次送审，合法
    await t.waitChipGone();                           // 第二次的结论回来：合法，状态条消失
    await release('validate');                        // 第一次（旧几何）的越界结论此刻才到
    assert.deepEqual(await t.legalNotices(), [], '旧几何的越界提示不得弹到新的几何上');
    assert.equal(await t.chip(), null, '状态仍是合法，不回到「未通过」');
    const afterLateVerdict = await t.trackedStatuses();
    assert.equal(afterLateVerdict.filter(([, status]) => status === 'invalid').length, 0, '协调器里没有被标成非法的面');
    checks.push('同一个面连续改两次：前一次几何的越界结论晚到，不提示、不改状态');
    await t.undo();
    await t.undo();
    await t.waitChipGone();

    // ── 6. 校验期间撤销：晚到的越界结论不提示；重做后重新校验并提示 ──
    backend.validate.plan.push({ mode: 'hold', verdict: OUTSIDE });
    await t.drawPolygon(faceA);
    await t.waitChip(/校验中 1 个面/);
    const validateRequestsBeforeUndo = requests.validate.length;
    await t.undo();                                   // 面被撤销掉了：校验中的记录随之消失
    await t.waitChipGone();
    await release('validate');                        // 越界的结论此刻才到
    assert.deepEqual(await t.legalNotices(), [], '撤销之后才回来的越界提示不得弹出');
    assert.equal(await t.chip(), null, '撤销之后状态条不出现');
    // 重做：面回来了，重新送审；后端仍回越界 → 应当提示
    backend.validate.plan.push({ mode: 'verdict', verdict: OUTSIDE });
    await t.redo();
    await t.waitToast(/1 个面超出成果 ROI（矿山边界），保存时将被拒绝，请调整顶点位置/);
    await t.waitChip(/1 个面未通过校验/);
    assert.equal(requests.validate.length, validateRequestsBeforeUndo + 1, '重做恢复出来的面重新送审一次');
    await shot('07-redo-revalidated');
    await t.waitToastsCleared();
    await t.undo();                                   // 回到没有这个面的状态
    await t.waitChipGone();
    checks.push('校验期间撤销：晚到的越界结论不提示；重做后重新校验，越界就提示');

    // ── 7. 校验期间切换成果：旧成果的结论不落到新成果上 ──
    const switchResult = async (resultId) => {
      await page.evaluate((url) => {
        window.history.pushState({}, '', url);
        window.dispatchEvent(new PopStateEvent('popstate'));
      }, `/classification-results/editor?project_id=${PROJECT_ID}&result_id=${resultId}`);
      // 有未保存的编辑时会先弹「未保存提醒」，确认后才会切换；没有未保存的编辑时不弹，直接切换
      const dialog = page.locator('.el-message-box');
      await Promise.race([
        dialog.locator('button', { hasText: CONFIRM_DISCARD }).waitFor({ timeout: 5000 }),
        page.waitForFunction((id) => new URLSearchParams(window.location.search).get('result_id') === String(id)
          && !document.querySelector('.el-overlay'), resultId, { timeout: 5000 }),
      ]).catch(async (error) => {
        console.log('switch FAILED snapshot', JSON.stringify(await page.evaluate(() => ({
          href: location.pathname + location.search,
          state: document.querySelector('.toolbar-state')?.innerText?.replace(/\s+/g, ' '),
          dialogs: document.querySelectorAll('.el-message-box').length,
          buttons: [...document.querySelectorAll('.el-message-box button')].map((node) => [node.innerText.trim(), node.className, node.getBoundingClientRect().width]),
        }))));
        throw error;
      });
      if (await dialog.count()) await dialog.locator('button', { hasText: CONFIRM_DISCARD }).click({ timeout: 5000 });
      await page.waitForFunction((id) => new URLSearchParams(window.location.search).get('result_id') === String(id)
        && document.querySelector('.classification-map canvas.maplibregl-canvas') && !document.querySelector('.el-overlay'), resultId, { timeout: 15000 });
      await page.waitForTimeout(1200);
    };
    backend.validate.plan.push({ mode: 'hold', verdict: OUTSIDE });
    await t.drawPolygon(faceA);
    await t.waitChip(/校验中 1 个面/);
    await switchResult(OTHER_RESULT_ID);
    assert.equal(await t.featureCount(), 1, '切换成果后画布是新成果的面');
    await release('validate');
    assert.deepEqual(await t.legalNotices(), [], '旧成果的结论不得提示到新成果上');
    assert.equal(await t.chip(), null, '新成果上没有校验状态');
    await switchResult(RESULT_ID);
    checks.push('校验期间切换成果：旧成果的结论不落到新成果上');

    // ── 8. 保存与校验同时发生：保存始终由后端裁定 ──
    backend.validate.plan.push({ mode: 'hold', verdict: OUTSIDE });
    await t.drawPolygon(faceA);
    await t.waitChip(/校验中 1 个面/);
    backend.save = 'ok';
    await t.toolbarButton('保存新版本').click();
    await page.locator('.el-message', { hasText: '已保存为 V1' }).waitFor();
    await t.waitChipGone();
    await release('validate');                        // 结论此刻才到：画布已换成服务端的新版本，判为过期
    assert.deepEqual(await t.legalNotices(), [], '保存之后的晚到结论不提示');
    assert.equal(await t.chip(), null);
    // 保存成功后画布上是服务端规范化的新版本：原有的面 + 刚保存的面
    const baseCount = await t.featureCount();
    assert.equal(baseCount, 2, '保存成功：画布是服务端返回的新版本');
    assert.deepEqual(await t.trackedStatuses().then((items) => items.map(([, status]) => status)), [null, null], '新版本的面都不再需要跟踪');
    await t.waitToastsCleared();
    // 校验拿不到结论（断网）时保存：后端说第 2 个面越界 → 422，指明第几个面
    backend.validate.plan.push({ mode: 'network' });
    await t.drawPolygon(faceA);
    await t.waitChip(/1 个面未确认/);
    backend.save = '422';
    const savesBefore = requests.save.length;
    await t.waitToastsCleared();
    await t.toolbarButton('保存新版本').click();
    const lastSave = requests.save.at(-1);
    const submittedTotal = lastSave.feature_collection.features.length;
    await t.waitToast(new RegExp(`无法保存：第 2 个面（共 ${submittedTotal} 个）几何必须完全位于成果 ROI 内`));
    assert.equal(requests.save.length, savesBefore + 1, '保存请求照发：编辑期校验未确认不拦保存');
    assert.equal(await t.featureCount(), baseCount + 1, '保存被拒：画布不变（多出来的是刚画的那个面）');
    assert.match((await t.chip()).text, /1 个面未确认/, '保存被拒不把「未确认」改写成编辑期的结论');
    await shot('08-save-while-unconfirmed');
    await t.waitToastsCleared();
    await t.undo();                                   // 回到已保存的状态
    await t.waitChipGone();
    checks.push('保存与校验同时发生：保存成功后晚到的结论作废；校验未确认时保存照发，由后端裁定');

    // ── 9. 合并失败：画布与撤销栈保持原样 ──
    await t.resetView();                              // 上一步保存 422 时地图被放大定位到了出错的面
    const left = t.quadAt(0.44, 0.52, 0.042, 0.036);
    const right = t.quadAt(0.482, 0.52, 0.042, 0.036);
    await t.drawPolygon(left);
    await t.drawPolygon(right);
    const countWithPair = baseCount + 2;
    assert.equal(await t.featureCount(), countWithPair);
    const selectBoth = async () => {
      await t.selectFace(t.centroid(left));
      await page.keyboard.down('Shift');
      await t.clickMap(t.centroid(right));
      await page.keyboard.up('Shift');
      await page.waitForFunction(() => /已选择 2 个面/.test(document.querySelector('.toolbar-state')?.textContent || ''), null, { timeout: 5000 });
    };
    await selectBoth();
    const beforeMerge = await t.canvas();
    const historyBeforeMerge = await t.historyFlags();
    for (const [mode, expected] of [['http500', /^合并失败/], ['malformed', /^合并失败：合并服务没有返回有效的面$/]]) {
      backend.union.mode = mode;
      const unionsBefore = requests.union.length;
      await t.clickTool('合并选中');
      await t.waitToast(expected);
      await t.waitButtonIdle('合并选中');
      assert.equal(requests.union.length, unionsBefore + 1, '合并请求已发出');
      assert.deepEqual(await t.canvas(), beforeMerge, `${mode}：画布逐值不变`);
      assert.deepEqual(await t.historyFlags(), historyBeforeMerge, `${mode}：撤销栈不变`);
      assert.equal(await t.featureCount(), countWithPair, `${mode}：没有多出合并面`);
      await t.waitToastsCleared();
    }
    await shot('09-merge-failed-untouched');
    // 成功的合并：两个面变成一个；撤销能逐值回到原样
    backend.union.mode = 'ok';
    await t.clickTool('合并选中');
    await t.waitToast(/^已合并为 1 个面（原 2 个）$/);
    await t.waitButtonIdle('合并选中');
    assert.equal(await t.featureCount(), countWithPair - 1, '合并后少一个面');
    await t.undo();
    assert.deepEqual(await t.canvas(), beforeMerge, '撤销合并：画布逐值回到合并前');
    checks.push('合并遇到 HTTP 500 / 响应格式异常：画布与撤销栈逐值不变；成功后可撤销');

    // ── 10. 简化失败：画布与撤销栈保持原样 ──
    await t.drawPolygon(t.quadAt(0.44, 0.30, 0.06, 0.05));
    await t.selectFace(t.at(0.47, 0.325));
    const beforeSimplify = await t.canvas();
    const historyBeforeSimplify = await t.historyFlags();
    for (const [mode, expected] of [['http500', /^简化失败/], ['malformed', /^简化失败：简化服务返回的结果与送审的面对不上$/]]) {
      backend.simplify.mode = mode;
      await t.clickTool('消除锯齿');
      await t.waitToast(expected);
      await t.waitButtonIdle('消除锯齿');
      assert.deepEqual(await t.canvas(), beforeSimplify, `${mode}：画布逐值不变`);
      assert.deepEqual(await t.historyFlags(), historyBeforeSimplify, `${mode}：撤销栈不变`);
      await t.waitToastsCleared();
    }
    checks.push('简化遇到 HTTP 500 / 响应格式异常：画布与撤销栈逐值不变');

    // ── 11. 简化的各种结果各有提示，原面都不动 ──
    backend.simplify.mode = 'ok';
    for (const [outcome, expected] of [
      ['drift', /^1 个面简化后面积或形态变化会超过精度上限，已跳过，原面保持不变$/],
      ['outside', /^1 个面简化后会超出矿山边界或几何无效，已跳过，原面保持不变$/],
      ['unchanged', /^1 个面在精度要求内没有可安全去除的顶点，保持原样$/],
    ]) {
      backend.simplify.outcome = outcome;
      await t.clickTool('消除锯齿');
      await t.waitToast(expected);
      await t.waitButtonIdle('消除锯齿');
      assert.deepEqual(await t.canvas(), beforeSimplify, `${outcome}：原面不动`);
      await t.waitToastsCleared();
    }
    checks.push('简化的拒绝原因（越界 / 形态超限）与「没有可去的顶点」各有提示，原面不动');

    // ── 12. 简化成功；没改过的面不重复提交，改过之后可以再简化 ──
    backend.simplify.outcome = 'changed';
    await t.clickTool('消除锯齿');
    await t.waitToast(/^已简化 1 个面的边缘，面积最大变化 0\.31%$/);
    await t.waitButtonIdle('消除锯齿');
    const afterSimplify = await t.canvas();
    assert.equal(afterSimplify.length, beforeSimplify.length, '图斑数不变');
    assert.deepEqual(afterSimplify.slice(0, -1), beforeSimplify.slice(0, -1), '其余的面逐值不变');
    assert.deepEqual(afterSimplify.at(-1), { geometry: SIMPLIFIED_GEOMETRY, class_code: beforeSimplify.at(-1).class_code }, '这个面换成了后端返回的几何，地类保留');
    await t.waitToastsCleared();
    // 重新选中简化后的面，再点一次：不发请求（接口不保证幂等），提示说明没有重复简化
    const simplifiedPoint = await t.toScreen(SIMPLIFIED_CENTROID);
    await t.selectFace(simplifiedPoint);
    const simplifiesBefore = requests.simplify.length;
    await t.clickTool('消除锯齿');
    await t.waitToast(/^1 个面已是消除锯齿后的结果，没有重复简化；改动过之后才能再次消除锯齿$/);
    await t.waitButtonIdle('消除锯齿');
    assert.equal(requests.simplify.length, simplifiesBefore, '没改过的面不重复提交');
    assert.deepEqual(await t.canvas(), afterSimplify, '画布不变');
    await shot('10-simplified-not-resubmitted');
    // 改过之后可以再简化
    await t.waitToastsCleared();
    await t.enterVertexMode(simplifiedPoint);
    await t.dragLastVertex(1, 16, 10);
    assert.notDeepEqual((await t.canvas()).at(-1).geometry, SIMPLIFIED_GEOMETRY, '顶点已拖动');
    backend.simplify.outcome = 'unchanged';
    await t.clickTool('消除锯齿');
    await t.waitToast(/^1 个面在精度要求内没有可安全去除的顶点，保持原样$/);
    assert.equal(requests.simplify.length, simplifiesBefore + 1, '改过的面重新提交');
    await t.waitButtonIdle('消除锯齿');
    await t.waitToastsCleared();
    checks.push('简化成功：只换这一个面；没改过的面不重复提交，改过之后可以再简化');

    // ── 13. 简化期间继续修改：被改过的面不套用结果 ──
    backend.simplify.outcome = 'changed';
    await t.selectFace(simplifiedPoint);
    const countBefore = await t.featureCount();
    backend.simplify.mode = 'hold';
    await t.clickTool('消除锯齿');
    await page.waitForTimeout(600);
    // 请求在途：仍能拖动这个面的顶点
    await t.enterVertexMode(simplifiedPoint);
    await t.dragLastVertex(2, -16, 10);
    const draggedGeometry = (await t.canvas()).at(-1).geometry;
    backend.simplify.mode = 'ok';
    await release('simplify');
    await t.waitToast(/^1 个面在简化期间被修改，本次没有简化；需要的话请重新选择$/);
    await t.waitButtonIdle('消除锯齿');
    assert.equal(await t.featureCount(), countBefore, '图斑数不变');
    assert.deepEqual((await t.canvas()).at(-1).geometry, draggedGeometry, '被拖过的面保持拖动后的几何，没有换成后端的简化结果');
    await t.waitToastsCleared();
    checks.push('简化请求在途时继续修改图斑：这个面不套用结果，并说明原因');

    // ── 14. 请求超时的结果 ──
    const timeoutResult = await timeoutRunSafe;
    assert.ok(!timeoutResult.interrupted, `超时场景被打断：${timeoutResult.interrupted}`);
    assert.ok(timeoutResult.elapsed >= 14000 && timeoutResult.elapsed < 26000, `超时应在 15 秒附近触发，实际 ${timeoutResult.elapsed} ms`);
    assert.match(timeoutResult.chip.text, /1 个面未确认/);
    assert.ok(timeoutResult.texts.some((text) => text.includes('1 个面的合法性未能确认（请求超时）；可以继续编辑，保存时以后端校验为准')),
      `超时提示应写明原因，实际：${JSON.stringify(timeoutResult.texts)}`);
    checks.push(`请求 15 秒没有回来：状态条为「未确认」，提示写明「请求超时」（实测 ${timeoutResult.elapsed} 毫秒）`);

    // 收尾核对：三个几何接口的请求体只带几何本身，前端不传容差
    for (const body of [...requests.validate, ...requests.simplify, ...requests.union]) {
      assert.ok(Array.isArray(body?.geometries) && body.geometries.length >= 1, '几何接口的请求体应是 geometries 数组');
      body.geometries.forEach((geometry) => assert.deepEqual(Object.keys(geometry).sort(), ['coordinates', 'type'], '几何接口只能带几何本身'));
    }
    assert.ok(requests.simplify.every((body) => !('tolerance' in body)), '前端不传 tolerance：容差由后端决定');
    // 夹具故意制造的失败（断网、500、404）会在控制台留下资源加载错误；其余都不该有
    const unexpectedConsole = consoleErrors.filter((text) => !/Failed to load resource|net::ERR_CONNECTION_FAILED|status of 500|status of 404/.test(text));
    assert.deepEqual(pageErrors, [], '浏览器运行时异常');
    assert.deepEqual(unexpectedConsole, [], '非夹具制造的控制台错误');
    await fs.writeFile(path.join(out, 'result.json'), JSON.stringify({
      scope: '无头 Chromium + HTTP fixture；验证编辑期校验与合并 / 简化异常处理，不代表真实后端',
      browser: browser.version(), checks, requests: Object.fromEntries(Object.entries(requests).map(([key, value]) => [key, value.length])),
      pageErrors, consoleErrors,
    }, null, 2));
    console.log(`通过 ${checks.length} 组检查，截图与结果在 ${out}`);
    for (const item of checks) console.log(`  ✓ ${item}`);
  } catch (error) {
    await shot('99-failure').catch(() => {});
    // 失败瞬间的画布与工具区：让失败说得出原因，而不是只有一句超时
    const atFailure = t ? await t.editor([
      'return { features: state.draw.getAll().features.map((f) => ({ cls: f.properties.class_code, first: f.geometry.coordinates[0][0] })),',
      '  mode: state.draw.getMode(), selected: state.draw.getSelectedIds().length, merging: state.merging, simplifying: state.simplifying, saving: state.saving,',
      '  toolbar: document.querySelector(".toolbar-state")?.innerText?.replace(/\\s+/g, " "),',
      '  buttons: [...document.querySelectorAll(".editor-toolbar button")].map((b) => [b.textContent.trim(), b.disabled, b.classList.contains("is-loading")]),',
      '  canUndo: state.canUndo, canRedo: state.canRedo };',
    ].join('\n')).catch((problem) => ({ dumpFailed: String(problem) })) : null;
    console.log('AT FAILURE:', JSON.stringify(atFailure), 'union requests', requests.union.length, 'simplify requests', requests.simplify.length);
    await fs.writeFile(path.join(out, 'result.json'), JSON.stringify({ ok: false, checks, pageErrors, consoleErrors, error: String(error), errorStack: String(error.stack || '').slice(0, 1500), atFailure }, null, 2));
    throw error;
  } finally {
    await fixture.flushHeld().catch(() => {});
    await context.close().catch(() => {});
    await browser.close();
  }
}
// 作为模块引入时只导出工具（给排障脚本复用），不自动运行
module.exports = { createBackend, installFixture, createTools, OUTSIDE, OK, SIMPLIFIED_GEOMETRY, UNION_GEOMETRY };
if (require.main === module) main().catch((error) => { console.error(error); process.exitCode = 1; });
