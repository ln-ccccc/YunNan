// 分类矢量编辑器 —— 顶点删除拦截与提示的真实浏览器回归（2026-11-24 审计 A1/E 组）。
//
// 为什么需要真实浏览器：删除防护挂在 mapbox-gl-draw 的 direct_select.onTrash（删除按钮与
// Delete/Backspace 键盘两条路都汇到这里），Node 单测只能用假处理器验证模式对象。首次交付时
// 面确实被保住了，但提示写成 $message.warning(文案, { grouping: true })——Element Plus 的第二个
// 参数是应用上下文，消息组件渲染时直接抛 "Object prototype may only be an Object or null"，
// 用户看不到任何解释。只有加载真实消息组件才能发现。
// 第三个：无底图提示是盖在地图右下角的纯文字浮层，曾拦截鼠标——落在它下面的点击画不出
// 顶点，回车后草稿被丢弃，页面不报任何错。下面的 clickMap 每次点击前先确认落点是画布。
// 第四个：画布上的 Delete/Backspace 从来没有生效过（mapbox-gl-draw 的键盘处理只认 mapboxgl-canvas，
// 这里是 MapLibre 的 maplibregl-canvas）。而本脚本曾据此报"Delete 键被拦且有说明"——那是上一步点删除
// 按钮时弹出的提示还没消失，被断言误认。现在断言"按键后出现的新提示"：按键前先等提示条清空。
// 第五个（2026-11-24 通读绘制库后补）：画到一半的草稿没有地类、环也不闭合，只在绘制模式的状态里被
// 引用。以前未完成就保存会报"class_code 必须为 0 到 5 的整数"；Ctrl+Z 把草稿变成幽灵（之后点击没反应、
// 回车草稿凭空消失）；Delete/Backspace 直接丢掉整个草稿。库按"渲染在最上层的点要素"取按下目标，
// 相邻顶点在屏幕上只隔几像素时点顶点会多出一个顶点。这些都只有真实浏览器能验证。
//
// HTTP fixture 只在测试浏览器内拦截：不登录真实账号、不写入真实项目、不访问真实后端。
// 从仓库根目录运行（PowerShell；路径换成自己的独立安装/输出目录）：
//   $env:PLAYWRIGHT_PACKAGE='D:/Temp/.../node_modules/playwright'
//   $env:GEOVIEW_UI_URL='http://127.0.0.1:4402'
//   $env:GUI_AUDIT_OUTPUT='D:/Temp/YunNan-browser-results'
//   node tests/browser/geoview-editor-vertex-guard-smoke.cjs
// 默认无头 + 真 GPU（SwiftShader 软渲染可能不出帧，见 testing-playbook 技巧 30）；
// 设 AUDIT_HEADFUL=1 用可见浏览器。可用 AUDIT_BROWSER_EXECUTABLE 指定 Chromium。
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_PACKAGE || 'playwright');

const baseUrl = process.env.GEOVIEW_UI_URL || 'http://127.0.0.1:4402';
const out = process.env.GUI_AUDIT_OUTPUT || path.join(os.tmpdir(), 'yunnan-editor-vertex-guard');
const RESULT_ID = 142;
const classes = ['草地', '林地', '建筑', '道路', '裸地', '水体'].map((name, index) => ({
  class_code: index, class_name_zh: name,
  class_name: ['grassland', 'forest', 'building', 'road', 'bareground', 'water'][index],
}));
const initialFeatureCollection = {
  type: 'FeatureCollection',
  features: [{
    type: 'Feature',
    properties: { class_code: 1, class_name: 'forest', feature_id: 'auto-142-1' },
    geometry: { type: 'Polygon', coordinates: [[[102.70, 25.05], [102.72, 25.05], [102.72, 25.07], [102.70, 25.07], [102.70, 25.05]]] },
  }],
};
const roiGeometry = { type: 'Polygon', coordinates: [[[102.68, 25.03], [102.74, 25.03], [102.74, 25.09], [102.68, 25.09], [102.68, 25.03]]] };

async function main() {
  await fs.mkdir(out, { recursive: true });
  const checks = [];
  const missing = [];
  const pageErrors = [];
  // 最后一页才把夹具成果标成"已被取代"：告警会把地图往下推约 85px，
  // 前面场景的落点都是按没有告警的版面算的
  let superseded = false;
  const browser = await chromium.launch({
    headless: process.env.AUDIT_HEADFUL !== '1',
    executablePath: process.env.AUDIT_BROWSER_EXECUTABLE || undefined,
    args: ['--use-gl=angle', '--use-angle=d3d11', '--ignore-gpu-blocklist'],
  });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
  await context.route('**/api/**', async (route) => {
    const request = route.request();
    const endpoint = new URL(request.url()).pathname;
    const headers = {
      'Access-Control-Allow-Origin': baseUrl, 'Access-Control-Allow-Credentials': 'true',
      'Access-Control-Allow-Headers': 'content-type', 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    };
    if (request.method() === 'OPTIONS') { await route.fulfill({ status: 204, headers }); return; }
    let data;
    if (endpoint === '/api/auth/session') {
      data = { authenticated: true, username: '视觉测试管理员' };
    } else if (endpoint.endsWith(`/classification-results/${RESULT_ID}`)) {
      data = {
        result_id: RESULT_ID, project_id: 42, mine_fid: 1, year: 2024, vector_status: 'ready',
        current_revision_no: 0, classes, current_feature_collection: initialFeatureCollection,
        roi_geometry: roiGeometry, map_manifest: { project_id: 42, bounds: [102.68, 25.03, 102.74, 25.09], api_tile_url: null },
        ...(superseded ? { effective: false } : {}),
      };
    } else if (request.method() === 'GET' && endpoint.endsWith(`/classification-results/${RESULT_ID}/revisions`)) {
      data = { current_revision_no: 0, revisions: [{ revision_no: 0, source: 'auto', feature_count: 1, created_at: '2026-10-09T10:00:00Z' }] };
    } else if (request.method() === 'POST' && endpoint.endsWith(`/classification-results/${RESULT_ID}/revisions`)) {
      // 后端对 ROI 外的要素返回 422，details 带 feature_index（提交 FC 的 0 起下标）
      await route.fulfill({ status: 422, headers, json: { success: false, code: 1, msg: '分类成果保存数据不合法', data: { details: { field: 'geometry', reason: '几何必须完全位于成果 ROI 内', feature_index: 1 } } } });
      return;
    } else { missing.push(endpoint); await route.fulfill({ status: 404, headers, json: { success: false, code: 1, msg: '未提供测试 fixture' } }); return; }
    await route.fulfill({ headers, json: { code: 0, success: true, data, count: Array.isArray(data) ? data.length : 0 } });
  });
  const page = await context.newPage();
  // 任何未捕获异常都算失败：首次交付的缺陷正是"面保住了，提示组件渲染时抛错"
  page.on('pageerror', (error) => pageErrors.push(String(error.stack || error).slice(0, 400)));
  const shot = async (name) => page.screenshot({ path: path.join(out, `${name}.png`) });
  const toasts = async () => (await page.locator('.el-message__content').allInnerTexts()).map((text) => text.trim());
  const stateText = async () => (await page.locator('.toolbar-state').innerText()).replace(/\s+/g, ' ').trim();
  try {
    await page.goto(`${baseUrl}/classification-results/editor?project_id=42&result_id=${RESULT_ID}`, { waitUntil: 'domcontentloaded' });
    await page.locator('.classification-map canvas.maplibregl-canvas').waitFor({ timeout: 30000 });
    await page.waitForTimeout(1500);
    const box = await page.locator('.classification-map canvas.maplibregl-canvas').boundingBox();
    const elementAt = (point) => page.evaluate(({ x, y }) => {
      const element = document.elementFromPoint(x, y);
      return element ? `${element.tagName}.${String(element.className).split(' ')[0]}` : '空';
    }, point);
    // 提示浮层、地图控件会吞掉落在它们下面的地图点击：表现是"工具退出了、什么都没画出来"，
    // 页面不报错。每次点击前确认落点是画布，让这种失败说得出原因。
    const clickMap = async (point) => {
      const hit = await elementAt(point);
      assert.equal(hit, 'CANVAS.maplibregl-canvas', `地图点击 (${Math.round(point.x)}, ${Math.round(point.y)}) 落在 ${hit} 上，不是画布`);
      await page.mouse.move(point.x - 3, point.y - 2, { steps: 4 });
      await page.mouse.move(point.x, point.y, { steps: 3 });
      await page.mouse.down(); await page.mouse.up();
    };
    const centroid = (points) => ({
      x: points.reduce((sum, point) => sum + point.x, 0) / points.length,
      y: points.reduce((sum, point) => sum + point.y, 0) / points.length,
    });
    // ---- 面板里写明键盘操作（此前的说明一个键都没提）。默认收起，展开也不得撑高地图所在的这一行 ----
    // 说明放在"版本记录"下方的空白处。曾放在左侧地类面板：面板只有约 170px 宽，说明展开后约
    // 220px 高，把这一行从 650px 撑到 843px、地图卡片下方空出一大块；给面板封顶后又被裁得只露
    // 半行，要在面板里滚动才看得到（2026-11-24 在真实浏览器里量出，下面两条行高断言就是防它）
    const hints = page.locator('.key-hints');
    assert.equal(await hints.getAttribute('open'), null, '键盘说明应默认收起');
    const rowBefore = (await page.locator('.editor-workspace').boundingBox()).height;
    assert.ok(rowBefore <= 660, `工作区这一行的高度应由地图决定（650px），实际 ${Math.round(rowBefore)}px`);
    await hints.locator('summary').click();
    await page.waitForTimeout(200);
    const hintText = await hints.innerText();
    for (const key of ['回车', 'Esc', 'Backspace', 'Delete', 'Ctrl']) assert.ok(hintText.includes(key), `键盘说明应包含 ${key}，实际：${hintText}`);
    const rowAfter = (await page.locator('.editor-workspace').boundingBox()).height;
    assert.ok(Math.abs(rowAfter - rowBefore) <= 2, `展开键盘说明不得改变工作区行高（${Math.round(rowBefore)}px → ${Math.round(rowAfter)}px）`);
    await shot('00-key-hints');
    await hints.locator('summary').click();
    checks.push('面板写明键盘操作（默认收起，展开也不撑高工作区）');
    // 读编辑器实例只为核对模式、草稿顶点数与把经纬度换成屏幕坐标；所有编辑动作仍走真实键鼠
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
    const mode = () => editor('return state.draw.getMode();');
    const featureCount = () => editor('return state.draw.getAll().features.length;');
    // 草稿 = 没有地类的那个要素。draw.getAll() 给的是 GeoJSON：外环 = 已放置的顶点 + 跟随鼠标的点 + 闭合点，
    // 所以已放置顶点数 = 外环坐标数 − 2；没有草稿时为 -1
    const sketchPlaced = () => editor("const draft = state.draw.getAll().features.find((feature) => feature.properties.class_code === undefined); return draft ? draft.geometry.coordinates[0].length - 2 : -1;");
    const selectedVertices = () => editor('return state.draw.getSelectedPoints().features.length;');
    // 最近一个要素各顶点与各边中点的屏幕坐标
    const lastRingSize = () => editor('const features = state.draw.getAll().features; return features[features.length - 1].geometry.coordinates[0].length;');
    const ringPoints = () => editor([
      "const canvas = document.querySelector('.classification-map canvas.maplibregl-canvas').getBoundingClientRect();",
      'const features = state.draw.getAll().features;',
      'const ring = features[features.length - 1].geometry.coordinates[0];',
      'const toScreen = (lngLat) => { const point = state.map.project(lngLat); return { x: canvas.x + point.x, y: canvas.y + point.y }; };',
      'const vertices = ring.slice(0, -1).map(toScreen);',
      'const midpoints = vertices.map((vertex, index) => { const next = vertices[(index + 1) % vertices.length]; return { x: (vertex.x + next.x) / 2, y: (vertex.y + next.y) / 2 }; });',
      'return { vertices, midpoints };',
    ].join('\n'));
    const vertexAt = (index) => editor('const features = state.draw.getAll().features; return features[features.length - 1].geometry.coordinates[0][arg];', index);
    const initialView = await editor('return { center: state.map.getCenter(), zoom: state.map.getZoom() };');
    const restoreView = async () => {
      await editor('state.map.jumpTo({ center: arg.center, zoom: arg.zoom });', initialView);
      await page.waitForTimeout(700);
    };
    // 键盘事件只认以画布为目标的事件(mapbox-gl-draw 检查事件目标的类名)。真实用户按回车/
    // Delete 前刚点过画布, 焦点天然在画布上; 自动化里焦点可能被下载、对话框等带走到 body,
    // 所以按键前显式把焦点放回画布——测的是"焦点在画布上时按键是否生效", 不是焦点归属。
    const focusCanvas = async () => {
      await page.locator('.classification-map canvas.maplibregl-canvas').focus();
    };
    // 提示条默认停留 3 秒。"按键后应出现提示"的断言必须以清空为前提，否则上一步残留的提示会让它假通过
    const waitToastsCleared = async () => {
      await page.waitForFunction(() => !document.querySelector('.el-message'), null, { timeout: 15000 });
    };

    const drawPolygon = async (points) => {
      await page.click('.mapbox-gl-draw_polygon');
      for (const point of points) await clickMap(point);
      await focusCanvas();
      await page.keyboard.press('Enter');
      await page.waitForTimeout(500);
    };

    // ---- 三角面：按钮删除与 Delete 键盘都必须被拦，且必须给出看得见的说明 ----
    const a = { x: box.x + box.width * 0.12, y: box.y + box.height * 0.70 };
    const triangle = [a, { x: a.x + 150, y: a.y }, { x: a.x + 75, y: a.y + 125 }];
    await drawPolygon(triangle);
    assert.match(await stateText(), /已选择 1 个面/, '新画的三角面应处于选中态');
    await clickMap(centroid(triangle));   // 已选中的面再点一次进入顶点模式
    await page.waitForTimeout(400);
    await clickMap(a);                    // 选中一个顶点（同时让画布获得焦点）
    await page.waitForTimeout(300);
    await page.click('.mapbox-gl-draw_trash');
    await page.waitForTimeout(400);
    let messages = await toasts();
    assert.ok(messages.some((text) => text.includes('只剩 2 个顶点')), `按钮删除被拦时应有说明提示，实际：${JSON.stringify(messages)}`);
    assert.match(await stateText(), /已选择 1 个面/, '三角面不得被整面删除');
    await shot('01-triangle-button-blocked');
    checks.push('三角面按钮删除被拦且有说明');

    await clickMap(a);
    await waitToastsCleared();
    await focusCanvas();
    await page.keyboard.press('Delete');
    await page.waitForTimeout(400);
    messages = await toasts();
    assert.ok(messages.some((text) => text.includes('只剩 2 个顶点')), `Delete 键盘删除被拦时应有说明提示（按键前提示条已清空），实际：${JSON.stringify(messages)}`);
    assert.match(await stateText(), /已选择 1 个面/, 'Delete 键盘不得把三角面整面删除');
    await shot('02-triangle-keyboard-blocked');
    checks.push('三角面 Delete 键盘删除被拦且有说明');

    // ---- 四边形：Shift 选两个被拦；只选一个能删；再删被拦 ----
    const d = { x: box.x + box.width * 0.12, y: box.y + box.height * 0.22 };
    const quad = [d, { x: d.x + 170, y: d.y }, { x: d.x + 170, y: d.y + 115 }, { x: d.x, y: d.y + 115 }];
    await drawPolygon(quad);
    await clickMap(centroid(quad));
    await page.waitForTimeout(400);
    await page.keyboard.down('Shift'); await clickMap(quad[0]); await clickMap(quad[1]); await page.keyboard.up('Shift');
    await page.waitForTimeout(300);
    await page.click('.mapbox-gl-draw_trash');
    await page.waitForTimeout(400);
    messages = await toasts();
    assert.ok(messages.some((text) => text.includes('只剩 2 个顶点')), '四边形一次删两个顶点应被拦');
    assert.match(await stateText(), /已选择 1 个面/, '四边形不得被整面删除');
    await shot('03-quad-two-vertices-blocked');
    // 只删一个顶点是合法编辑：用 Delete 键删（顶点是否真的少了一个，在下面导出草稿时核对坐标数）
    await waitToastsCleared();
    await clickMap(quad[2]);
    await focusCanvas();
    await page.keyboard.press('Delete');
    await page.waitForTimeout(500);
    assert.match(await stateText(), /存在未保存编辑.*已选择 1 个面/s, '只删一个顶点是合法编辑，面应保留');
    assert.deepEqual(await toasts(), [], '合法删除不应有任何提示');
    await shot('04-quad-one-vertex-deleted');
    // 剩三个顶点时再删应被拦：Delete 键与删除按钮各验一次，都要求是按键/点击之后新出现的提示
    await clickMap({ x: quad[0].x + 1, y: quad[0].y + 1 });
    await focusCanvas();
    await page.keyboard.press('Delete');
    await page.waitForTimeout(400);
    messages = await toasts();
    assert.ok(messages.some((text) => text.includes('只剩 2 个顶点')), `剩三个顶点时 Delete 键再删应被拦，实际：${JSON.stringify(messages)}`);
    assert.match(await stateText(), /已选择 1 个面/, 'Delete 键不得把面删掉');
    await waitToastsCleared();
    await page.click('.mapbox-gl-draw_trash');
    await page.waitForTimeout(400);
    messages = await toasts();
    assert.ok(messages.some((text) => text.includes('只剩 2 个顶点')), `剩三个顶点时删除按钮再删应被拦，实际：${JSON.stringify(messages)}`);
    assert.match(await stateText(), /已选择 1 个面/, '删除按钮不得把面删掉');
    checks.push('四边形 Shift 删两个被拦 / Delete 键删一个生效 / 再删时键与按钮都被拦');

    // ---- 草稿导出：按钮文案与下载内容（纯客户端，不经过 fixture 的保存接口）----
    assert.equal((await page.locator('.toolbar-actions button', { hasText: 'GeoJSON' }).innerText()).trim(), '导出草稿 GeoJSON');
    const [download] = await Promise.all([
      page.waitForEvent('download', { timeout: 15000 }),
      page.locator('.toolbar-actions button', { hasText: 'GeoJSON' }).click(),
    ]);
    assert.match(download.suggestedFilename(), new RegExp(`^classification-result-${RESULT_ID}-draft-v0-unsaved\\.geojson$`));
    const stream = await download.createReadStream();
    let raw = '';
    for await (const chunk of stream) raw += chunk;
    const draft = JSON.parse(raw);
    const ringSize = (feature) => feature.geometry.coordinates[0].length;
    const latitudeOf = (feature) => feature.geometry.coordinates[0].reduce((sum, point) => sum + point[1], 0) / ringSize(feature);
    const clientFeatures = draft.features.filter((feature) => String(feature.properties.feature_id).startsWith('client-'));
    assert.ok(clientFeatures.length >= 2, `草稿应包含未保存的新面，实际 ${clientFeatures.length} 个`);
    // Delete 键删掉了四边形的一个顶点：外环由 5 个坐标（含闭合点）变成 4 个。三角面也是 4 个；
    // 四边形画在画面左上，纬度更高
    const fourPoint = clientFeatures.filter((feature) => ringSize(feature) === 4);
    assert.equal(fourPoint.length, 2, `三角面与删过一个顶点的四边形都应是 4 个坐标，实际各面坐标数：${clientFeatures.map(ringSize)}`);
    fourPoint.sort((p, q) => latitudeOf(q) - latitudeOf(p));
    assert.ok(latitudeOf(fourPoint[0]) > latitudeOf(fourPoint[1]) + 0.005, '纬度较高的那个应是四边形');
    checks.push('脏状态下导出的是草稿而非已保存版本');

    // ---- 放弃全部修改需要确认 ----
    // 消息框有淡入过渡：DOM 出现的瞬间还是全透明，这时截图拍不到它（Playwright 的可见判定
    // 不看透明度）。等它完全不透明再拍；先等前面的提示条消失，免得挡住画面。
    const waitForMessageBox = async () => page.waitForFunction(() => {
      const box = document.querySelector('.el-message-box');
      const overlay = box && box.closest('.el-overlay');
      return Boolean(overlay && getComputedStyle(overlay).opacity === '1' && getComputedStyle(box).opacity === '1');
    }, null, { timeout: 5000 });
    await page.waitForFunction(() => !document.querySelector('.el-message'), null, { timeout: 10000 });
    await page.getByRole('button', { name: '放弃全部修改' }).click();
    await waitForMessageBox();
    assert.match(await page.locator('.el-message-box').innerText(), /将丢弃当前全部未保存的编辑/, '确认框应说明后果');
    await shot('05-discard-confirm');
    await page.getByRole('button', { name: '继续编辑' }).click();
    await page.waitForTimeout(400);
    assert.match(await stateText(), /存在未保存编辑/, '取消后编辑应保留');
    await page.getByRole('button', { name: '放弃全部修改' }).click();
    await waitForMessageBox();
    await page.locator('.el-message-box__btns button', { hasText: '放弃修改' }).click();
    await page.waitForTimeout(600);
    assert.match(await stateText(), /当前版本已保存/, '确认后应回到已保存状态');
    assert.equal((await page.locator('.toolbar-actions button', { hasText: 'GeoJSON' }).innerText()).trim(), '导出已保存 GeoJSON');
    checks.push('放弃全部修改需要确认');
    // ---- 无底图提示是纯文字浮层，不得拦截它下方地图的点击 ----
    // 夹具没有底图，地图右下角一直显示这条提示。两个待合并的面特意画在它的上边缘两侧。
    const notice = page.locator('.map-notice');
    assert.equal(await notice.isVisible(), true, '夹具没有底图，应显示无底图提示');
    const noticeBox = await notice.boundingBox();
    const underNotice = { x: noticeBox.x + noticeBox.width - 24, y: noticeBox.y + noticeBox.height / 2 };
    assert.equal(await elementAt(underNotice), 'CANVAS.maplibregl-canvas', '无底图提示没有可点内容，不得拦截它下方地图的点击');
    checks.push('无底图提示浮层不拦截地图点击');

    // ---- 合并：同类可用、异类置灰 ----
    const m = { x: noticeBox.x + 23, y: noticeBox.y - 30 };
    const p1 = [m, { x: m.x + 110, y: m.y }, { x: m.x + 110, y: m.y + 80 }, { x: m.x, y: m.y + 80 }];
    const p2 = [{ x: m.x + 130, y: m.y }, { x: m.x + 240, y: m.y }, { x: m.x + 240, y: m.y + 80 }, { x: m.x + 130, y: m.y + 80 }];
    await drawPolygon(p1);
    await drawPolygon(p2);
    // p1 的上半部分与原有的大图斑（林地）重叠：选中 p1 必须点它在大图斑下边缘以下的那一块，
    // 点重叠处选中的是大图斑（视口固定 1440×1000，大图斑下边缘在提示浮层顶边上方约 10px）
    const p1Center = { x: m.x + 55, y: m.y + 70 };
    const p2Center = { x: m.x + 185, y: m.y + 70 };
    const blank = { x: box.x + box.width * 0.50, y: box.y + box.height * 0.06 };
    await page.keyboard.down('Shift'); await clickMap(p1Center); await page.keyboard.up('Shift');
    await page.waitForTimeout(400);
    const mergeButton = page.getByRole('button', { name: '合并选中' });
    assert.equal(await mergeButton.isEnabled(), true, `同类选中时合并应可用（状态：${await stateText()}）`);
    await clickMap(blank);   // 点空白清空选择
    await page.waitForTimeout(300);
    await clickMap(p1Center);
    await page.waitForTimeout(300);
    await page.locator('.class-option', { hasText: '建筑' }).click();
    await page.waitForTimeout(300);
    await page.keyboard.down('Shift'); await clickMap(p2Center); await page.keyboard.up('Shift');
    await page.waitForTimeout(400);
    assert.equal(await mergeButton.isEnabled(), false, '异类选中时合并应置灰');
    assert.equal(await mergeButton.getAttribute('title'), '仅支持合并同地类的面');
    await shot('06-merge-mixed-disabled');
    checks.push('异类选中时合并置灰并说明');

    // ---- 保存 422：定位到出错图斑并把序号告诉用户 ----
    await page.getByRole('button', { name: '保存新版本' }).click();
    await page.waitForTimeout(900);
    messages = await toasts();
    assert.ok(messages.some((text) => /无法保存：第 2 个面（共 3 个）几何必须完全位于成果 ROI 内，已在地图上选中并定位/.test(text)),
      `422 提示应给出序号并定位，实际：${JSON.stringify(messages)}`);
    await shot('07-save-422-located');
    checks.push('保存 422 时提示序号并在地图上定位');

    // ---- 2026-11-24：绘制进行中的键盘与"整幅画布"操作 ----
    await page.getByRole('button', { name: '放弃全部修改' }).click();
    await waitForMessageBox();
    await page.locator('.el-message-box__btns button', { hasText: '放弃修改' }).click();
    await page.waitForTimeout(600);
    await restoreView();                      // 422 定位会移动视野，先回到初始视野
    const cleanCount = await featureCount();
    assert.equal(await mode(), 'simple_select');
    const savePosts = [];
    page.on('request', (request) => { if (request.method() === 'POST' && request.url().includes('/revisions')) savePosts.push(request.url()); });
    const undo = async () => {
      await focusCanvas();
      await page.keyboard.down('Control'); await page.keyboard.press('z'); await page.keyboard.up('Control');
      await page.waitForTimeout(350);
    };

    const s = { x: box.x + box.width * 0.08, y: box.y + box.height * 0.40 };
    const squareAt = (origin) => [origin, { x: origin.x + 60, y: origin.y }, { x: origin.x + 60, y: origin.y + 60 }, { x: origin.x, y: origin.y + 60 }];
    await page.click('.mapbox-gl-draw_polygon');
    for (const point of squareAt(s)) await clickMap(point);
    assert.equal(await mode(), 'draw_polygon');
    assert.equal(await sketchPlaced(), 4, '点 4 下后草稿应有 4 个已放置的顶点');
    // Delete/Backspace 退回刚放置的点，而不是丢掉整个草稿
    await focusCanvas();
    await page.keyboard.press('Backspace');
    await page.waitForTimeout(300);
    assert.equal(await sketchPlaced(), 3, 'Backspace 应退回一个点');
    assert.equal(await mode(), 'draw_polygon', '还有点可退时不退出绘制');
    await page.keyboard.press('Delete');
    await page.waitForTimeout(300);
    assert.equal(await sketchPlaced(), 2, 'Delete 同样退回一个点');
    // 绘制中 Ctrl+Z 也是退回一个点，草稿不能变成幽灵
    await undo();
    assert.equal(await sketchPlaced(), 1, '绘制中 Ctrl+Z 应退回一个点');
    assert.equal(await mode(), 'draw_polygon');
    await page.keyboard.press('Backspace');
    await page.waitForTimeout(300);
    assert.equal(await sketchPlaced(), 0, '退到一个已放置的点都不剩');
    assert.equal(await mode(), 'draw_polygon', '一个点都没放时仍留在绘制模式');
    await page.keyboard.press('Backspace');
    await page.waitForTimeout(400);
    assert.equal(await mode(), 'simple_select', '空草稿再退回：丢弃并回到选择模式');
    assert.equal(await sketchPlaced(), -1, '丢弃后不得留下草稿要素');
    assert.equal(await featureCount(), cleanCount);
    assert.match(await stateText(), /当前版本已保存/);
    checks.push('绘制中 Backspace / Delete / Ctrl+Z 退回上一个点，空草稿才丢弃');

    // Esc 取消绘制，不留下任何要素
    await page.click('.mapbox-gl-draw_polygon');
    for (const point of squareAt(s).slice(0, 3)) await clickMap(point);
    assert.equal(await mode(), 'draw_polygon');
    await focusCanvas();
    await page.keyboard.press('Escape');
    await page.waitForTimeout(400);
    assert.equal(await mode(), 'simple_select', 'Esc 应取消绘制');
    assert.equal(await featureCount(), cleanCount, '取消的草稿不得留下要素');
    checks.push('Esc 取消绘制');

    // 未完成绘制时保存 / 导出草稿：明确拦截并指引，不发请求、不动草稿
    await drawPolygon(squareAt(s));
    assert.equal(await featureCount(), cleanCount + 1);
    await page.click('.mapbox-gl-draw_polygon');
    const half = [{ x: s.x + 220, y: s.y }, { x: s.x + 280, y: s.y }, { x: s.x + 280, y: s.y + 60 }];
    for (const point of half) await clickMap(point);
    assert.equal(await mode(), 'draw_polygon');
    assert.equal(await sketchPlaced(), 3);
    await page.getByRole('button', { name: '保存新版本' }).click();
    await page.waitForTimeout(600);
    messages = await toasts();
    assert.ok(messages.some((text) => text.includes('有正在绘制、还没完成的面')), `未完成绘制时保存应被拦截并指引，实际：${JSON.stringify(messages)}`);
    assert.equal(savePosts.length, 0, '未完成绘制时不得发出保存请求');
    assert.equal(await mode(), 'draw_polygon', '拦截后草稿原样保留');
    assert.equal(await sketchPlaced(), 3, '拦截后已放置的顶点一个不少');
    await shot('08-sketch-save-blocked');
    await waitToastsCleared();
    let downloads = 0;
    const onDownload = () => { downloads += 1; };
    page.on('download', onDownload);
    await page.locator('.toolbar-actions button', { hasText: '导出草稿 GeoJSON' }).click();
    await page.waitForTimeout(900);
    page.off('download', onDownload);
    assert.equal(downloads, 0, '未完成绘制时不得导出草稿');
    assert.ok((await toasts()).some((text) => text.includes('有正在绘制、还没完成的面')), '导出草稿被拦截时也要说明原因');
    // 回车完成绘制：草稿成为新面
    await focusCanvas();
    await page.keyboard.press('Enter');
    await page.waitForTimeout(500);
    assert.equal(await mode(), 'simple_select');
    assert.equal(await featureCount(), cleanCount + 2, '回车应完成绘制');
    checks.push('未完成绘制时保存与导出草稿被拦截并指引，回车后照常完成');

    // 放弃全部修改：画到一半也要一起丢弃，并且退出绘制模式
    await page.click('.mapbox-gl-draw_polygon');
    const lower = { x: s.x + 220, y: s.y + 150 };
    for (const point of [{ ...lower }, { x: lower.x + 60, y: lower.y }, { x: lower.x + 60, y: lower.y + 60 }]) await clickMap(point);
    assert.equal(await mode(), 'draw_polygon');
    await page.getByRole('button', { name: '放弃全部修改' }).click();
    await waitForMessageBox();
    await page.locator('.el-message-box__btns button', { hasText: '放弃修改' }).click();
    await page.waitForTimeout(700);
    assert.equal(await mode(), 'simple_select', '放弃全部修改要先结束绘制');
    assert.equal(await featureCount(), cleanCount);
    assert.match(await stateText(), /当前版本已保存/);
    checks.push('放弃全部修改会连同画到一半的草稿一起丢弃');

    // 绘制中撤销之后草稿还能接着画（修复前 Ctrl+Z 会让草稿变成幽灵：之后点击没反应、回车草稿消失）
    await page.click('.mapbox-gl-draw_polygon');
    for (const point of [{ ...lower }, { x: lower.x + 60, y: lower.y }, { x: lower.x + 60, y: lower.y + 60 }]) await clickMap(point);
    await undo();
    assert.equal(await sketchPlaced(), 2, '点了三个点、撤销一次后草稿还剩两个已放置的点');
    for (const point of [{ x: lower.x, y: lower.y + 60 }, { x: lower.x + 60, y: lower.y + 60 }]) await clickMap(point);
    assert.equal(await sketchPlaced(), 4, '撤销过的草稿还能继续加点');
    await focusCanvas();
    await page.keyboard.press('Enter');
    await page.waitForTimeout(500);
    assert.equal(await mode(), 'simple_select');
    assert.equal(await featureCount(), cleanCount + 1, '撤销过的草稿仍能完成绘制');
    await undo();
    assert.equal(await featureCount(), cleanCount, '撤销应移除刚完成的草稿');
    assert.match(await stateText(), /当前版本已保存/);
    checks.push('绘制中撤销后草稿仍可继续绘制并完成（不再是幽灵草稿）');

    // ---- 就近命中：相邻顶点在屏幕上只隔几像素时，点顶点必须选中顶点，不能多出顶点 ----
    // 先在间距宽松时画一个锯齿边界，再用滚轮缩小视野把间距压到 8px 左右（与真实用户缩小看全局时一致）
    const z = { x: box.x + box.width * 0.42, y: box.y + box.height * 0.10 };
    const zigzag = [];
    for (let index = 0; index <= 8; index += 1) zigzag.push({ x: z.x + index * 30, y: index % 2 ? z.y + 20 : z.y });
    zigzag.push({ x: z.x + 240, y: z.y + 100 }, { x: z.x, y: z.y + 100 });
    await drawPolygon(zigzag);
    assert.match(await stateText(), /已选择 1 个面/, '新画的锯齿面应处于选中态');
    let points = await ringPoints();
    let spacing = Math.hypot(points.vertices[1].x - points.vertices[0].x, points.vertices[1].y - points.vertices[0].y);
    const zoomTarget = centroid(points.vertices);
    for (let notch = 0; spacing > 9 && notch < 12; notch += 1) {
      await page.mouse.move(zoomTarget.x, zoomTarget.y);
      await page.mouse.wheel(0, 240);
      await page.waitForTimeout(250);
      points = await ringPoints();
      spacing = Math.hypot(points.vertices[1].x - points.vertices[0].x, points.vertices[1].y - points.vertices[0].y);
    }
    await page.waitForTimeout(600);
    points = await ringPoints();
    spacing = Math.hypot(points.vertices[1].x - points.vertices[0].x, points.vertices[1].y - points.vertices[0].y);
    assert.ok(spacing > 3 && spacing <= 9, `相邻顶点间距应压到 9px 以内（中点标记落进顶点的点击范围），实际 ${spacing.toFixed(1)}px`);
    // 新画的锯齿面叠在夹具原有的林地大图斑上。先点空白清除选中，再点小面的下半部分
    // （那里没有顶点）：选中的必须是小面。修复前选中的是大图斑——库的"面按面积从小到大
    // 命中"从未生效（它给面积函数传的是 Feature，@mapbox/geojson-area 返回 undefined）
    const bottomY = Math.max(...points.vertices.map((vertex) => vertex.y));
    await clickMap({ x: box.x + box.width * 0.5, y: box.y + 20 });
    await page.waitForTimeout(300);
    assert.equal(await editor('return state.draw.getSelectedIds().length;'), 0, '点空白应清除选中');
    await clickMap({ x: zoomTarget.x, y: bottomY - 4 });
    await page.waitForTimeout(400);
    assert.equal(await mode(), 'simple_select');
    assert.equal(
      await editor('const features = state.draw.getAll().features; return state.draw.getSelectedIds()[0] === features[features.length - 1].id;'),
      true,
      '点叠在大图斑上的小图斑，选中的应是小图斑',
    );
    await shot('09-small-parcel-over-big-selected');
    checks.push('叠在大图斑上的小图斑能被单独点中');
    // 已选中的面再点一次进入顶点模式
    await clickMap({ x: zoomTarget.x, y: bottomY - 4 });
    await page.waitForTimeout(500);
    assert.equal(await mode(), 'direct_select', '点击已选中的面应进入顶点模式');
    const ringBefore = await lastRingSize();
    await shot('10-dense-vertices-vertex-mode');
    for (const index of [2, 3, 4]) {
      await clickMap(points.vertices[index]);
      await page.waitForTimeout(250);
      const picked = await editor('return state.draw.getSelectedPoints().features.map((feature) => feature.geometry.coordinates);');
      assert.deepEqual(picked, [await vertexAt(index)], `点第 ${index + 1} 个顶点应恰好选中它自己，实际选中：${JSON.stringify(picked)}`);
      assert.equal(await lastRingSize(), ringBefore, `点第 ${index + 1} 个顶点不得多出顶点`);
    }
    // 点在两个顶点正中间（小圆点被顶点圆盖住）：同样不会新增顶点
    await clickMap(points.midpoints[3]);
    await page.waitForTimeout(250);
    assert.equal(await lastRingSize(), ringBefore, '间距很小时点边的中点不得新增顶点');
    await shot('11-dense-vertices-vertex-selected');
    checks.push(`相邻顶点只隔 ${spacing.toFixed(1)}px 时点顶点恰好选中该顶点、不新增顶点`);

    // Esc：退出顶点模式（保留选中）→ 再按一次清除选中
    await focusCanvas();
    await page.keyboard.press('Escape');
    await page.waitForTimeout(300);
    assert.equal(await mode(), 'simple_select', 'Esc 应退出顶点模式');
    assert.match(await stateText(), /已选择 1 个面/, '退出顶点模式后仍保持选中');
    await page.keyboard.press('Escape');
    await page.waitForTimeout(300);
    assert.equal(await editor('return state.draw.getSelectedIds().length;'), 0, '普通选择下再按 Esc 应清除选中');
    assert.doesNotMatch(await stateText(), /已选择/, '清除选中后工具栏不再显示选中数');
    checks.push('Esc 退出顶点模式并保留选中，再按一次清除选中');

    // 回到宽松间距：点边上的小圆点仍然可以加顶点
    await restoreView();
    await clickMap({ x: zoomTarget.x, y: zoomTarget.y + 60 });
    await page.waitForTimeout(400);
    assert.equal(await mode(), 'simple_select', '撤销选中后点击应重新选中');
    await clickMap({ x: zoomTarget.x, y: zoomTarget.y + 60 });
    await page.waitForTimeout(400);
    assert.equal(await mode(), 'direct_select');
    points = await ringPoints();
    const wide = Math.hypot(points.vertices[1].x - points.vertices[0].x, points.vertices[1].y - points.vertices[0].y);
    assert.ok(wide > 20, `回到初始视野后相邻顶点应分得开，实际 ${wide.toFixed(1)}px`);
    const beforeAdd = await lastRingSize();
    await clickMap(points.midpoints[0]);
    await page.waitForTimeout(300);
    assert.equal(await lastRingSize(), beforeAdd + 1, '间距宽松时点边上的小圆点应新增一个顶点');
    assert.equal(await selectedVertices(), 1, '新增的顶点处于选中态，可以直接拖动');
    await shot('12-midpoint-adds-vertex');
    checks.push('间距宽松时点边上的小圆点仍可新增顶点');
    await page.getByRole('button', { name: '放弃全部修改' }).click();
    await waitForMessageBox();
    await page.locator('.el-message-box__btns button', { hasText: '放弃修改' }).click();
    await page.waitForTimeout(600);
    assert.match(await stateText(), /当前版本已保存/);

    // ---- 贴边的小图斑不抢点击：点击点落在大图斑里、只是离小图斑的边不到 2px，选中的仍是大图斑 ----
    // 2026-11-24 在真实成果上抓到：判定"点击点在哪个面里"的查询把描边图层也算上了，而描边的
    // 命中带容差——挨着点击点的一条边也被算成"点在它里面"，于是同时"包含"的面有两个，规则退回
    // "取最小的面"，选中的是旁边的小面（点道路面选中了贴边的建筑面）。包含判定必须只看填充图层。
    {
      const forestRight = await editor('const ring = state.draw.getAll().features[0].geometry.coordinates[0]; const canvas = document.querySelector(\'.classification-map canvas.maplibregl-canvas\').getBoundingClientRect(); return Math.max(...ring.map((point) => state.map.project(point).x)) + canvas.x;');
      const top = box.y + box.height * 0.45;
      // 紧贴大图斑右边缘、在外面画一个 44×60 的小面（与大图斑共用一条边）
      const beside = [{ x: forestRight, y: top }, { x: forestRight + 44, y: top }, { x: forestRight + 44, y: top + 60 }, { x: forestRight, y: top + 60 }];
      await drawPolygon(beside);
      assert.equal(await featureCount(), 2);
      await clickMap({ x: box.x + box.width * 0.5, y: box.y + 20 });     // 点空白清除选中
      await page.waitForTimeout(300);
      assert.equal(await editor('return state.draw.getSelectedIds().length;'), 0, '点空白应清除选中');
      // 在大图斑里、离公共边不到半个像素处点击：选中的必须是大图斑。（1px 时恰好落在描边
      // 命中容差之外，触发不了这个问题；真实成果上点道路面时离贴边建筑面的边更近）
      await clickMap({ x: forestRight - 0.4, y: top + 20 });
      await page.waitForTimeout(400);
      assert.equal(
        await editor('const features = state.draw.getAll().features; return state.draw.getSelectedIds()[0] === features[0].id;'),
        true,
        '点击点在大图斑里、只是离贴边小图斑不到 2px 时，选中的应是大图斑',
      );
      await shot('12b-adjacent-small-does-not-steal-click');
      // 反过来，点在小图斑中间：选中的是小图斑
      await clickMap({ x: forestRight + 22, y: top + 20 });
      await page.waitForTimeout(400);
      assert.equal(
        await editor('const features = state.draw.getAll().features; return state.draw.getSelectedIds()[0] === features[features.length - 1].id;'),
        true,
        '点贴边小图斑的中间，选中的应是小图斑',
      );
      checks.push('贴边的小图斑不抢点击：点在大图斑里选大图斑，点在小图斑里选小图斑');
      await page.getByRole('button', { name: '放弃全部修改' }).click();
      await waitForMessageBox();
      await page.locator('.el-message-box__btns button', { hasText: '放弃修改' }).click();
      await page.waitForTimeout(600);
      assert.match(await stateText(), /当前版本已保存/);
    }

    assert.deepEqual(savePosts, [], `本轮不得发出任何保存请求：${savePosts.join(' | ')}`);

    // ---- 被取代的成果要明说，且告警不得顶掉工具栏（v-if / v-else 链） ----
    // 概览、台账、变化矩阵、溯源只认"同矿山同年 id 最大"的那条成果；生产 3036 条可编辑成果里
    // 2387 条已被取代，此前编辑器不提示：保存成功，却哪里都不体现
    superseded = true;
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.locator('.classification-map canvas.maplibregl-canvas').waitFor({ timeout: 30000 });
    await page.waitForTimeout(1200);
    const supersededAlert = page.locator('.editor-alert', { hasText: '该成果已被同矿山同年更新的成果取代' });
    assert.equal(await supersededAlert.isVisible(), true, '已被取代的成果应显示告警');
    assert.match(await supersededAlert.innerText(), /不会体现在概览、台账、变化矩阵与溯源中/, '告警要说清修订会去哪里、不会去哪里');
    assert.equal(await page.locator('.editor-toolbar').isVisible(), true, '告警之后工具栏必须仍然显示');
    assert.equal(await page.locator('.class-option').count(), 6, '告警之后地类面板必须仍然显示');
    // 只提示、不禁用：契约允许保存任意 ready / ready_empty 成果。告警把地图下推了约 85px，
    // 这里在可见部分的上半区画面
    const alertBox = await page.locator('.classification-map canvas.maplibregl-canvas').boundingBox();
    await drawPolygon([{ x: alertBox.x + alertBox.width * 0.40, y: alertBox.y + alertBox.height * 0.30 }, { x: alertBox.x + alertBox.width * 0.46, y: alertBox.y + alertBox.height * 0.30 }, { x: alertBox.x + alertBox.width * 0.46, y: alertBox.y + alertBox.height * 0.36 }]);
    assert.equal(await page.getByRole('button', { name: '保存新版本' }).isEnabled(), true, '被取代的成果仍然可以保存');
    await shot('13-superseded-alert');
    checks.push('被取代的成果显示告警且不挤掉工具栏，仍可保存');
    assert.deepEqual(pageErrors, [], `页面不得有未捕获异常：${pageErrors.join(' | ')}`);
    assert.deepEqual(missing, [], `出现未提供 fixture 的接口：${missing.join(', ')}`);
    await fs.writeFile(path.join(out, 'result.json'), JSON.stringify({ ok: true, checks, pageErrors, missing }, null, 2));
    console.log(`通过 ${checks.length} 组检查，截图与结果在 ${out}`);
  } catch (error) {
    await shot('99-failure').catch(() => {});
    await fs.writeFile(path.join(out, 'result.json'), JSON.stringify({ ok: false, checks, pageErrors, missing, error: String(error) }, null, 2));
    throw error;
  } finally {
    await browser.close();
  }
}

main().catch((error) => { console.error(error); process.exit(1); });
