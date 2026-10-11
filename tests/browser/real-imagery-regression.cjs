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
  const cases = config.real_cases || [[9, 7503], [10, 7430]];
  assert.ok(Array.isArray(cases) && cases.length > 0, 'real_cases 必须包含至少一个真实项目/FID');
  assert.ok(cases.every(item => Array.isArray(item) && item.length === 2 && item.every(value => Number.isInteger(value) && value > 0)), 'real_cases 须为正整数项目ID/FID对');
  const out = process.env.GUI_AUDIT_OUTPUT;
  const miner = process.env.MINER_UI_URL || 'http://127.0.0.1:14712';
  const geo = process.env.GEOVIEW_UI_URL || 'http://127.0.0.1:14713';
  await fs.mkdir(out, { recursive: true });
  const browser = await chromium.launch({ headless: false });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, recordVideo: { dir: out } });
  const page = await context.newPage();
  const result = { fidelity: '真实 Flask/BFF + PANGAEA 7503/7430 + 2022-12-09 Google Earth 影像', checks: [], errors: [] };
  page.on('pageerror', error => result.errors.push(failureMessage(error, config)));
  const shot = async file => {
    await page.evaluate(async () => {
      await new Promise(requestAnimationFrame);
      await Promise.all(document.getAnimations().filter(animation => animation.effect?.getTiming().iterations !== Infinity).map(animation => animation.finished.catch(() => {})));
    });
    return page.screenshot({ path: path.join(out, file) });
  };
  try {
    await page.goto(miner);
    await page.getByPlaceholder('请输入管理员账号').fill(config.username);
    await page.getByPlaceholder('请输入密码', { exact: true }).fill(config.password);
    await page.getByRole('button', { name: '登录', exact: true }).click();
    await page.getByRole('heading', { name: '项目工作台', exact: true }).waitFor();
    await page.locator('.nav-list').getByRole('button', { name: '影像管理', exact: true }).click();
    for (const [project, fid] of cases) {
      await page.locator('#imagery-project').selectOption(String(project));
      const candidatesResponse = await context.request.get(`${miner}/api/projects/${project}/imagery/candidates`);
      assert.equal(candidatesResponse.status(), 200);
      const candidatesData = (await candidatesResponse.json()).data;
      const candidates = Array.isArray(candidatesData) ? candidatesData : candidatesData.items;
      const original = candidates.find(item => item.display_name.includes(String(fid)) && item.width > 1000);
      assert.ok(original?.bounds?.length === 4);
      const row = page.locator('.candidate-row:not(.head)').filter({ has: page.locator('.cand-name').getByText(original.display_name, { exact: true }) }).first();
      await row.click();
      const clip = page.locator('.tool-card').first();
      await clip.locator('select').first().selectOption('mine');
      await clip.locator('select').nth(1).selectOption(String(fid));
      for (const buffer of [0, 100, 250, 500]) {
        await clip.locator('input[type=number]').fill(String(buffer));
        const pending = page.waitForResponse(response => response.url().endsWith('/imagery/clip') && response.request().method() === 'POST');
        await clip.getByRole('button', { name: '执行裁剪', exact: true }).click();
        const response = await pending;
        const body = await response.json();
        assert.equal(response.status(), 200, JSON.stringify(body));
        assert.ok(body.data.width >= 8 && body.data.height >= 8);
        assert.ok(body.data.storage_key.startsWith(`projects/${project}/inputs/imagery/`));
        await clip.locator('.result-line.ok').waitFor();
        result.checks.push({ name: `矿山 ${fid} 实际界面 ${buffer}m 裁剪成功`, status: 'passed', request: response.request().postDataJSON(), data: body.data });
      }
      await shot(`${fid}-buffer-500m-success.png`);

      await clip.locator('select').first().selectOption('draw');
      const svg = clip.locator('.draw-svg');
      const box = await svg.boundingBox();
      const click = (x, y) => page.mouse.click(box.x + box.width * x, box.y + box.height * y);
      await click(.5, .25); await click(.5, .5);
      await page.mouse.dblclick(box.x + box.width * .5, box.y + box.height * .75);
      await clip.locator('input[type=number]').fill('100');
      const failedWait = page.waitForResponse(response => response.url().endsWith('/imagery/clip') && response.request().method() === 'POST');
      await clip.getByRole('button', { name: '执行裁剪', exact: true }).click();
      const failed = await failedWait;
      assert.equal(failed.status(), 400);
      await clip.locator('.result-line.error').waitFor();
      assert.equal(await clip.locator('.result-line.ok').count(), 0);
      await shot(`${fid}-invalid-draw-error.png`);
      result.checks.push({ name: `矿山 ${fid} 零面积框选即使带100m缓冲仍被真实后端拒绝，页面显示错误状态`, status: 'passed', http: failed.status() });

      await clip.getByRole('button', { name: '清除重画', exact: true }).click();
      await click(.25, .25); await click(.75, .25); await click(.75, .75);
      await page.mouse.dblclick(box.x + box.width * .25, box.y + box.height * .75);
      const drawnWait = page.waitForResponse(response => response.url().endsWith('/imagery/clip') && response.request().method() === 'POST');
      await clip.getByRole('button', { name: '执行裁剪', exact: true }).click();
      const drawn = await drawnWait;
      assert.equal(drawn.status(), 200);
      for (const [x, y] of drawn.request().postDataJSON().geometry.coordinates[0]) {
        assert.ok(x >= original.bounds[0] && x <= original.bounds[2]);
        assert.ok(y >= original.bounds[1] && y <= original.bounds[3]);
      }
      await clip.locator('.result-line.ok').waitFor();
      await shot(`${fid}-draw-correct-region.png`);
      result.checks.push({ name: `矿山 ${fid} 框选使用原影像真实经纬范围，失败后重试成功`, status: 'passed', geometry: drawn.request().postDataJSON().geometry });

      const slice = page.locator('.tool-card').nth(1);
      await slice.locator('input[type=number]').first().fill('1024');
      await slice.locator('input[type=number]').nth(1).fill('4');
      const slicedWait = page.waitForResponse(response => response.url().endsWith('/imagery/slice') && response.request().method() === 'POST');
      await slice.getByRole('button', { name: '执行切片', exact: true }).click();
      const sliced = await slicedWait;
      const slicedBody = await sliced.json();
      assert.equal(sliced.status(), 200, JSON.stringify(slicedBody));
      assert.equal(slicedBody.data.created, 4);
      await slice.locator('.slice-link').first().waitFor();
      const href = new URL(await slice.locator('.slice-link').first().getAttribute('href'));
      assert.equal(href.origin, new URL(geo).origin);
      assert.equal(href.searchParams.get('project_id'), String(project));
      await shot(`${fid}-slice-ready.png`);
      result.checks.push({ name: `矿山 ${fid} 自动切片真实落盘登记，去解译入口使用配置地址`, status: 'passed', data: slicedBody.data, href: href.href });
    }
    assert.deepEqual(result.errors, []);
  } catch (error) {
    result.fatal = failureMessage(error, config);
    process.exitCode = 1;
    await shot('failure.png').catch(() => {});
  } finally {
    await context.close();
    await browser.close();
    if (result.errors.length || result.fatal) process.exitCode = 1;
    await fs.writeFile(path.join(out, 'real-imagery-results.json'), JSON.stringify(result, null, 2));
    console.log(JSON.stringify({ checks: result.checks.length, errors: result.errors, fatal: result.fatal }));
  }
}
main().catch(() => { console.error('真实影像回归未完成：请检查非空 real_cases、私有配置 JSON、Playwright 安装、隔离服务和输出目录权限。'); process.exitCode = 1; });
