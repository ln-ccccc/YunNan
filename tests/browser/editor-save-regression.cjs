// 真实隔离后端保存，仅延迟已执行请求的回包以复现保存期间的用户操作。
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_PACKAGE || 'playwright');

function failureMessage(error, config) {
  let message = String(error?.message || error).split(/\r?\n/)[0];
  message = message.replace(/\b(?:cookie|set-cookie|authorization)\s*[:=].*$/gi, '[已脱敏]');
  for (const value of [config.username, config.password]) {
    if (typeof value === 'string' && value) message = message.split(value).join('[已脱敏]');
  }
  return message;
}

async function main() {
  const config = JSON.parse(await fs.readFile(process.env.DEEP_AUDIT_CONFIG, 'utf8'));
  const out = process.env.GUI_AUDIT_OUTPUT;
  const miner = process.env.MINER_UI_URL || 'http://127.0.0.1:14712';
  const geo = process.env.GEOVIEW_UI_URL || 'http://127.0.0.1:14713';
  const api = process.env.BACKEND_URL || 'http://127.0.0.1:15068';
  await fs.mkdir(out, { recursive: true });
  const browser = await chromium.launch({ headless: false });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, recordVideo: { dir: out } });
  const page = await context.newPage();
  const result = { fidelity: '真实服务及持久化 + 合成分类矢量；仅人为延迟真实保存回包', checks: [], errors: [], dialogs: [] };
  page.on('pageerror', error => result.errors.push(failureMessage(error, config)));
  let nextDialog = 'dismiss';
  page.on('dialog', async dialog => {
    result.dialogs.push({ type: dialog.type(), message: dialog.message(), action: nextDialog });
    await dialog[nextDialog]();
  });
  const detailUrl = `${api}/api/projects/${config.project_id}/classification-results/${config.result_id}`;
  const read = async () => {
    const response = await context.request.get(detailUrl);
    assert.equal(response.status(), 200);
    return (await response.json()).data;
  };
  const select = async () => {
    await page.locator('.mapbox-gl-draw_polygon').waitFor();
    const canvas = page.locator('.classification-map canvas');
    await canvas.scrollIntoViewIfNeeded();
    await page.evaluate(async () => {
      await Promise.all(document.getAnimations().filter(a => a.effect?.getTiming().iterations !== Infinity).map(a => a.finished.catch(() => {})));
    });
    const box = await canvas.boundingBox();
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    await page.locator('.selection-count').waitFor();
  };
  let release;
  let saveTimeout;
  try {
    await page.goto(miner);
    await page.getByPlaceholder('请输入管理员账号').fill(config.username);
    await page.getByPlaceholder('请输入密码', { exact: true }).fill(config.password);
    await page.getByRole('button', { name: '登录', exact: true }).click();
    await page.getByRole('heading', { name: '项目工作台', exact: true }).waitFor();
    await page.goto(`${geo}/classification-results/editor?project_id=${config.project_id}&result_id=${config.result_id}`);
    await select();
    const before = await read();
    const usedClasses = before.current_feature_collection.features.map(feature => feature.properties.class_code);
    const targetClass = [0, 1, 2, 3, 4, 5].find(code => !usedClasses.includes(code));
    assert.notEqual(targetClass, undefined);
    await page.locator('.class-option').nth(targetClass).click();
    await page.getByText('存在未保存编辑', { exact: false }).waitFor();

    // 取消重载和离开均必须保留本地未保存内容。
    let detailRequests = 0;
    page.on('request', request => { if (request.url() === detailUrl) detailRequests++; });
    await page.getByRole('button', { name: '重新加载', exact: true }).click();
    await page.getByText('存在未保存编辑', { exact: false }).waitFor();
    assert.equal(result.dialogs.length, 1);
    assert.equal(detailRequests, 0);
    await page.getByRole('button', { name: '返回分类结果', exact: true }).click();
    await page.getByText('存在未保存编辑', { exact: false }).waitFor();
    assert.equal(result.dialogs.length, 2);
    assert.ok(page.url().includes('/classification-results/editor'));
    await page.screenshot({ path: path.join(out, 'dirty-cancel-keeps-editor.png') });
    result.checks.push({ name: '取消脏编辑重载/离开后，编辑内容和页面保留，无额外详情请求', status: 'passed' });

    let delivered;
    let rejectSave;
    const realSaved = new Promise((resolve, reject) => { delivered = resolve; rejectSave = reject; });
    realSaved.catch(() => {});
    const gate = new Promise(resolve => { release = resolve; });
    const savePattern = '**/classification-results/*/revisions';
    await context.route(savePattern, async route => {
      if (route.request().method() !== 'POST') return route.continue();
      try {
        const response = await route.fetch();
        delivered({ status: response.status(), body: await response.json() });
        await gate;
        await route.fulfill({ response });
      } catch (error) {
        rejectSave(error);
        result.fatal ||= `保存请求失败：${failureMessage(error, config)}`;
        process.exitCode = 1;
        await route.abort().catch(() => {});
      }
    });
    await page.getByRole('button', { name: '保存新版本', exact: true }).click();
    const saved = await Promise.race([realSaved, new Promise((resolve, reject) => {
      saveTimeout = setTimeout(() => reject(new Error('真实保存请求30秒内未完成；检查请求是否发出及隔离后端')), 30000);
    })]);
    clearTimeout(saveTimeout);
    assert.equal(saved.status, 200);
    assert.equal((await read()).current_revision_no, before.current_revision_no + 1);
    assert.equal(await page.locator('.class-option:disabled').count(), 6);
    assert.equal(await page.locator('.classification-map').getAttribute('inert'), '');
    const canvasBox = await page.locator('.classification-map canvas').boundingBox();
    await page.mouse.click(canvasBox.x + canvasBox.width / 2, canvasBox.y + canvasBox.height / 2);
    assert.equal(await page.getByRole('button', { name: '重新加载', exact: true }).isDisabled(), true);
    await page.screenshot({ path: path.join(out, 'saving-real-response-held.png') });
    release();
    await page.getByText('当前版本已保存', { exact: false }).waitFor();
    await page.locator('.map-saving').waitFor({ state: 'hidden' });
    await context.unroute(savePattern);
    assert.equal(await page.locator('.classification-map').getAttribute('inert'), null);
    const after = await read();
    assert.ok(after.current_feature_collection.features.some(feature => feature.properties.class_code === targetClass));
    result.checks.push({ name: '真实保存回包延迟时地图/改类/重载一致锁定；收到回包后解锁，版本与服务端一致', status: 'passed', revision: after.current_revision_no });

    await page.reload();
    await select();
    await page.locator('.class-option').nth((targetClass + 1) % 6).click();
    await page.getByText('存在未保存编辑', { exact: false }).waitFor();
    nextDialog = 'accept';
    const reloaded = page.waitForResponse(response => response.url() === detailUrl && response.request().method() === 'GET');
    await page.getByRole('button', { name: '重新加载', exact: true }).click();
    assert.equal((await reloaded).status(), 200);
    await page.getByText('当前版本已保存', { exact: false }).waitFor();
    assert.deepEqual((await read()).current_feature_collection, after.current_feature_collection);
    await page.screenshot({ path: path.join(out, 'reload-confirmed-current-version.png') });
    result.checks.push({ name: '重开读到保存成果；确认放弃后来编辑后重载正式版本，服务端未被改写', status: 'passed' });
    assert.deepEqual(result.errors, []);
  } catch (error) {
    result.fatal = failureMessage(error, config);
    process.exitCode = 1;
    await page.screenshot({ path: path.join(out, 'failure.png') }).catch(() => {});
  } finally {
    release?.();
    clearTimeout(saveTimeout);
    await context.close();
    await browser.close();
    if (result.errors.length || result.fatal) process.exitCode = 1;
    await fs.writeFile(path.join(out, 'editor-save-results.json'), JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result));
  }
}
main().catch(() => { console.error('编辑保存回归未完成：请检查私有配置 JSON、Playwright 安装、隔离服务和输出目录权限。'); process.exitCode = 1; });
