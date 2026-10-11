import assert from 'node:assert/strict';
import test from 'node:test';
import http from 'node:http';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { fileURLToPath } from 'node:url';

const job = 'f019cb42-10af-4030-96a4-8a1a20edab89';
const downloadPath = `/api/projects/1/mines/original-imagery/${job}/download`;
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

async function withDownload(upstreamHandler, check) {
  const upstream = http.createServer(upstreamHandler);
  await new Promise(resolve => upstream.listen(0, '127.0.0.1', resolve));
  const child = spawn(process.execPath, [fileURLToPath(new URL('../test-support/downloadServer.mjs', import.meta.url))], {
    windowsHide: true,
    env: { ...process.env, GEOVIEW_BACKEND_URL: `http://127.0.0.1:${upstream.address().port}` },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let logs = '';
  child.stderr.on('data', chunk => { logs += chunk; });
  const exit = once(child, 'exit');
  try {
    const port = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('隔离 BFF 未启动')), 15000);
      child.stdout.once('data', chunk => { clearTimeout(timer); resolve(Number(String(chunk).trim())); });
      child.once('exit', () => { clearTimeout(timer); reject(new Error(`隔离 BFF 退出：${logs}`)); });
    });
    await check(`http://127.0.0.1:${port}${downloadPath}`, child);
  } finally {
    if (child.exitCode === null) child.kill();
    await exit;
    upstream.closeAllConnections();
    await new Promise(resolve => upstream.close(resolve));
  }
}

test('原始影像下载保持内容、附件名与 Cookie', async () => {
  await withDownload((req, res) => {
    assert.equal(req.headers.cookie, 'session=test-only');
    res.writeHead(200, { 'content-type': 'image/tiff', 'content-length': 7, 'content-disposition': 'attachment; filename="mine.tif"' });
    res.end('TESTTIF');
  }, async url => {
    const response = await fetch(url, { headers: { cookie: 'session=test-only' } });
    assert.equal(response.status, 200);
    assert.equal(await response.text(), 'TESTTIF');
    assert.equal(response.headers.get('content-disposition'), 'attachment; filename="mine.tif"');
  });
});

test('原始影像不存在时透传状态及具体错误', async () => {
  await withDownload((_req, res) => {
    res.writeHead(404, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ success: false, msg: '影像已不存在' }));
  }, async url => {
    const response = await fetch(url);
    assert.equal(response.status, 404);
    assert.equal((await response.json()).msg, '影像已不存在');
  });
});

test('上游中途断流只终止本次下载，BFF 仍可响应下一次请求', async () => {
  let first = true;
  await withDownload((_req, res) => {
    if (!first) { res.end('next'); return; }
    first = false;
    res.writeHead(200, { 'content-length': 64 });
    res.write('partial');
    setTimeout(() => res.destroy(), 100);
  }, async (url, child) => {
    await assert.rejects(async () => (await fetch(url)).arrayBuffer());
    await wait(50);
    assert.equal(child.exitCode, null);
    assert.equal(await (await fetch(url)).text(), 'next');
  });
});

test('正常流式 body 超过实际默认 30 秒连接超时仍能完成', { timeout: 40000 }, async () => {
  await withDownload((_req, res) => {
    res.writeHead(200, { 'content-type': 'image/tiff' });
    res.write('partial');
    const timer = setTimeout(() => res.end('done'), 31500);
    res.once('close', () => clearTimeout(timer));
  }, async (url, child) => {
    const started = performance.now();
    assert.equal(await (await fetch(url)).text(), 'partialdone');
    assert.ok(performance.now() - started >= 31000);
    assert.equal(child.exitCode, null);
  });
});

test('下游取消下载会提前关闭上游传输', async () => {
  let chunks = 0;
  let closedEarly = false;
  await withDownload((_req, res) => {
    res.writeHead(200, { 'content-length': 32768 });
    const timer = setInterval(() => {
      res.write(Buffer.alloc(1024));
      if (++chunks === 32) { clearInterval(timer); res.end(); }
    }, 25);
    res.once('close', () => { closedEarly = chunks < 32; clearInterval(timer); });
  }, async (url, child) => {
    const controller = new AbortController();
    const response = await fetch(url, { signal: controller.signal });
    await response.body.getReader().read();
    controller.abort();
    await wait(150);
    assert.equal(closedEarly, true);
    assert.ok(chunks < 32);
    assert.equal(child.exitCode, null);
  });
});

test('超大错误响应有界处理，返回受控网关错误', async () => {
  await withDownload((_req, res) => {
    res.writeHead(500, { 'content-type': 'text/plain' });
    res.end('x'.repeat(128 * 1024));
  }, async url => {
    const response = await fetch(url);
    assert.equal(response.status, 502);
    assert.equal((await response.json()).success, false);
  });
});
