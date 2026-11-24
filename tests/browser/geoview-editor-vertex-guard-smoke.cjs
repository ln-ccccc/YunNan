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
