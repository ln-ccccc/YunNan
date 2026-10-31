// 保存竞态回归（2026-10-31 审计 P1）：保存请求在途时产生的本地编辑不得被
// 响应静默覆盖。HTTP fixture 只在测试浏览器内拦截，不写真实项目数据。
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const { chromium } = require(process.env.PLAYWRIGHT_PACKAGE || 'playwright');

const baseUrl = process.env.GEOVIEW_UI_URL || 'http://127.0.0.1:4402';
const out = process.env.GUI_AUDIT_OUTPUT || path.join(os.tmpdir(), 'yunnan-editor-save-race');
const checks = [], errors = [], missing = [], consoleErrors = [], failedRequests = [];
const classes = ['草地', '林地', '建筑', '道路', '裸地', '水体'].map((name, index) => ({ class_code: index, class_name_zh: name, class_name: ['grassland','forest','building','road','bareground','water'][index] }));
const initialFeatureCollection = {
  type: 'FeatureCollection',
  features: [{
    type: 'Feature',
    id: 'auto-1',
    properties: { class_code: 1, class_name: 'forest' },
    geometry: { type: 'Polygon', coordinates: [[[102.70, 25.05], [102.72, 25.05], [102.72, 25.07], [102.70, 25.07], [102.70, 25.05]]] },
  }],
};
let serverRevision = 0;
const pendingSaves = [];
const savePayloads = [];

// 等待一个保存请求进入挂起状态（route handler 已捕获请求体）
async function waitForPendingSave(timeoutMs = 5000) {
  const deadline = Date.now() + timeoutMs;
  while (pendingSaves.length === 0) {
    if (Date.now() > deadline) throw new Error('保存请求未在时限内到达 fixture');
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  return pendingSaves[pendingSaves.length - 1].payload;
}

// 放行最早挂起的保存响应
function releaseSave() {
  const pending = pendingSaves.shift();
  assert.ok(pending, '没有挂起的保存响应可放行');
  pending.resolve();
}

// 等保存按钮恢复可点（saving 复位、loading 结束）
async function waitForSaveReady(page) {
  await page.waitForFunction(() => {
    const save = [...document.querySelectorAll('.editor-toolbar button')]
      .find(node => node.textContent.includes('保存新版本'));
    return save && !save.disabled && !save.classList.contains('is-loading');
  }, null, { timeout: 5000 });
}

async function clickCanvasAt(page, dx, dy) {
  const canvas = page.locator('.classification-map canvas');
  const box = await canvas.boundingBox();
  await page.mouse.click(box.x + box.width / 2 + dx, box.y + box.height / 2 + dy);
}

async function dblClickCanvasAt(page, dx, dy) {
  const canvas = page.locator('.classification-map canvas');
  const box = await canvas.boundingBox();
  await page.mouse.dblclick(box.x + box.width / 2 + dx, box.y + box.height / 2 + dy);
}

async function main() {
  await fs.mkdir(out, { recursive: true });
  const browser = await chromium.launch({ headless: false, executablePath: process.env.AUDIT_BROWSER_EXECUTABLE });
  const context = await browser.newContext({ reducedMotion: 'no-preference', viewport: { width: 1440, height: 1000 } });
  await context.route('**/api/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const endpoint = url.pathname;
    const headers = { 'Access-Control-Allow-Origin': baseUrl, 'Access-Control-Allow-Credentials': 'true', 'Access-Control-Allow-Headers': 'content-type', 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS' };
    if (request.method() === 'OPTIONS') { await route.fulfill({ status: 204, headers }); return; }
    let data;
    if (endpoint === '/api/auth/session') data = { authenticated: true, username: '视觉测试管理员' };
    else if (endpoint.endsWith('/classification-results/142')) {
      data = {
        result_id: 142, project_id: 42, mine_fid: 1, year: 2024,
        vector_status: 'ready', current_revision_no: serverRevision,
        classes, current_feature_collection: initialFeatureCollection,
      };
    } else if (request.method() === 'GET' && endpoint.endsWith('/classification-results/142/revisions')) {
      data = { revisions: [{ revision_no: serverRevision, source: 'auto', feature_count: initialFeatureCollection.features.length, created_at: '2026-10-09T10:00:00Z' }] };
    } else if (request.method() === 'POST' && endpoint.endsWith('/classification-results/142/revisions')) {
      // 保存挂起闸门：请求体先入库，等测试显式放行，模拟慢响应/晚到响应
      const payload = JSON.parse(request.postData() || '{}');
      savePayloads.push(payload);
      await new Promise(resolve => pendingSaves.push({ payload, resolve }));
      serverRevision += 1;
      await route.fulfill({
        headers,
        json: { code: 0, success: true, data: { feature_collection: payload.feature_collection, current_revision_no: serverRevision } },
      });
      return;
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
  const saveButton = page.getByRole('button', { name: '保存新版本', exact: true });
  const savedNotice = page.locator('.toolbar-state', { hasText: '当前版本已保存' });
  const dirtyNotice = page.locator('.toolbar-state', { hasText: '存在未保存编辑' });
  try {
    await page.goto(`${baseUrl}/classification-results/editor?project_id=42&result_id=142`);
    await page.locator('.classification-map canvas').waitFor();
    await page.locator('.mapbox-gl-draw_polygon').waitFor();
    await page.locator('.class-option').first().waitFor();
    await settle();

    // ── 用例 a：保存挂起期间改类入口冻结，改类不发生 ──
    await clickCanvasAt(page, 0, 0);
    await page.locator('.selection-count').waitFor();
    await page.locator('.class-option').nth(2).click();
    await dirtyNotice.waitFor();
    await saveButton.click();
    let payload = await waitForPendingSave();
    assert.equal(payload.base_revision_no, 0, '首次保存应基于初始版本号');
    const disabledStates = await page.locator('.class-option').evaluateAll(els => els.map(el => el.disabled));
    assert.ok(disabledStates.length >= 6 && disabledStates.every(state => state === true), '保存在途时改类按钮必须全部禁用');
    releaseSave();
    await savedNotice.waitFor();
    await page.locator('.el-message', { hasText: '已保存为 V1' }).waitFor();
    assert.equal(savePayloads[0].feature_collection.features[0].properties.class_code, 2, '首次保存应提交改类后的建筑');
    checks.push('用例a：保存挂起期间改类按钮全部禁用，放行后正常成功提示');

    // ── 用例 b：保存挂起期间真实绘图产生新修改，响应后保留并可再次保存 ──
    // 先改类制造脏状态（上一用例保存后按钮因 !isDirty 禁用）
    await clickCanvasAt(page, 0, 0);
    await page.locator('.selection-count').waitFor();
    await page.locator('.class-option').nth(1).click();
    await dirtyNotice.waitFor();
    await saveButton.click();
    await waitForPendingSave();
    // 保存期间通过绘制控件新建面（控件不受 saving 冻结，由代次+快照双比对兜底）
    await page.locator('.mapbox-gl-draw_polygon').click();
    await clickCanvasAt(page, -80, -100);
    await clickCanvasAt(page, 60, -90);
    await clickCanvasAt(page, 20, -50);
    await dblClickCanvasAt(page, -30, -60);
    releaseSave(); // 放行第 2 次保存（服务端落库 V2，画布上还有未保存的新面）
    await page.locator('.el-message--warning', { hasText: '保存期间又有新的修改' }).waitFor();
    await dirtyNotice.waitFor();
    await waitForSaveReady(page);
    // 再次保存：必须提交保存期间绘制的新面，且使用更新后的版本号
    await saveButton.click();
    payload = await waitForPendingSave();
    assert.equal(payload.base_revision_no, 2, '再次保存必须使用竞态分支更新后的版本号');
    assert.equal(payload.feature_collection.features.length, 2, '再次保存必须包含保存期间绘制的新面');
    releaseSave();
    await savedNotice.waitFor();
    await page.locator('.el-message', { hasText: '已保存为 V3' }).waitFor();
    checks.push('用例b：保存期间绘制的新面被保留，warning 提示再次保存后提交 2 个面与新版本号');

    // ── 用例 c：未结束手势（绘制中不闭合，draw.update 未发出、代次未变），
    //    放行响应时快照比对必须兜底保留画布 ──
    await clickCanvasAt(page, 0, 0);
    await page.locator('.selection-count').waitFor();
    await page.locator('.mapbox-gl-draw_trash').click();
    await dirtyNotice.waitFor();
    await saveButton.click();
    await waitForPendingSave();
    // 绘制 2 点不闭合：半成品已在 draw store 中（快照已变），但 draw.create
    // 尚未发出（代次未变）——正是代次漏掉、快照比对兜底的场景
    await page.locator('.mapbox-gl-draw_polygon').click();
    await clickCanvasAt(page, -70, 40);
    await clickCanvasAt(page, 50, 30);
    releaseSave(); // 放行第 4 次保存（服务端 V4）
    await page.locator('.el-message--warning', { hasText: '保存期间又有新的修改' }).waitFor();
    await dirtyNotice.waitFor();
    await waitForSaveReady(page);
    // 完成绘制：第 3 点 + 双击闭合（若响应覆盖过画布，此处将无法完成）
    await clickCanvasAt(page, 40, 90);
    await dblClickCanvasAt(page, -20, 80);
    await saveButton.click();
    payload = await waitForPendingSave();
    assert.equal(payload.base_revision_no, 4, '完成绘制后的保存必须使用 V4 基线');
    assert.equal(payload.feature_collection.features.length, 2, '删除 1 画 1 后再次保存应包含 2 个面');
    releaseSave();
    await savedNotice.waitFor();
    await page.locator('.el-message', { hasText: '已保存为 V5' }).waitFor();
    checks.push('用例c：绘制中未闭合手势放行响应由快照比对兜底保留，完成后再次保存提交 2 个面');

    assert.deepEqual(errors, [], '浏览器运行时异常');
    assert.deepEqual([...new Set(missing)], [], '未经 fixture 覆盖的 API');
    await fs.writeFile(path.join(out, 'save-race-results.json'), JSON.stringify({
      scope: '可见Chromium+HTTP fixture；验证保存竞态行为，不代表真实后端/GPU',
      browser: browser.version(), checks, savePayloads, errors, missing, consoleErrors, failedRequests,
    }, null, 2));
    console.log(JSON.stringify({ checks: checks.length, errors, missing, consoleErrors, failedRequests, out }, null, 2));
  } catch (error) {
    await page.screenshot({ path: path.join(out, 'save-race-failure.png') });
    await fs.writeFile(path.join(out, 'save-race-failure.json'), JSON.stringify({ checks, error: error.message, errors, missing, savePayloads }, null, 2));
    throw error;
  } finally {
    await context.close();
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
