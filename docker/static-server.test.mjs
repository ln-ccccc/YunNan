// 静态服务的行为测试。真实起一个服务进程（临时目录做站点根，随机空闲端口），用 fetch 发请求。
//
//     node --test docker/static-server.test.mjs
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const serverScript = fileURLToPath(new URL('./static-server.mjs', import.meta.url));
const HOME = '<html>站点首页</html>';
let root;
let base;
let child;

async function freePort() {
  const probe = net.createServer();
  await new Promise((resolve) => probe.listen(0, '127.0.0.1', resolve));
  const { port } = probe.address();
  await new Promise((resolve) => probe.close(resolve));
  return port;
}

before(async () => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), 'yunnan-static-server-'));
  for (const [folder, script] of [['js', 'app.11111111.js'], ['assets', 'index-AAAAAAAA.js']]) {
    fs.mkdirSync(path.join(root, folder));
    fs.writeFileSync(path.join(root, folder, script), 'window.served = "real";');
  }
  fs.writeFileSync(path.join(root, 'index.html'), HOME);
  const port = await freePort();
  base = `http://127.0.0.1:${port}`;
  child = spawn(process.execPath, [serverScript, root, String(port), '/index.html'], { stdio: 'ignore' });
  const deadline = Date.now() + 15000;
  for (;;) {
    try {
      if ((await fetch(`${base}/index.html`)).ok) return;
    } catch (error) {
      // 还没起来，稍后再试
    }
    if (Date.now() > deadline) throw new Error('静态服务 15 秒内没有起来');
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
});

after(() => {
  child.kill();
  fs.rmSync(root, { recursive: true, force: true });
});

// 把服务端回的那段脚本放进一个假的页面环境里执行：假的时钟、会话存储、confirm 和 reload。
// visit 返回 [问了几次, 刷新了几次]
function fakePage(storage) {
  const state = { now: 1_700_000_000_000, answer: true, questions: 0, reloads: 0 };
  const context = {
    Date: { now: () => state.now },
    sessionStorage: storage,
    window: {
      confirm: () => { state.questions += 1; return state.answer; },
      location: { reload: () => { state.reloads += 1; } },
    },
  };
  return {
    visit(script, secondsLater = 60, answer = true) {
      state.now += secondsLater * 1000;
      state.answer = answer;
      vm.runInNewContext(script, context);
      return [state.questions, state.reloads];
    },
  };
}

const workingStorage = () => {
  const store = new Map();
  return { getItem: (key) => store.get(key) ?? null, setItem: (key, value) => store.set(key, String(value)) };
};

async function staleScript() {
  return (await fetch(`${base}/js/362.deadbeef.js`)).text();
}

test('存在的脚本原样返回', async () => {
  for (const existing of ['/js/app.11111111.js', '/assets/index-AAAAAAAA.js']) {
    const response = await fetch(`${base}${existing}`);
    assert.equal(response.status, 200);
    assert.match(response.headers.get('content-type'), /^text\/javascript/);
    assert.equal(await response.text(), 'window.served = "real";');
  }
});

test('要的代码块已经不在：回一段问用户要不要刷新的脚本，不回落成首页', async () => {
  for (const missing of ['/js/362.deadbeef.js', '/assets/index-DeadBeef.js']) {
    const response = await fetch(`${base}${missing}`);
    assert.equal(response.status, 200);
    assert.match(response.headers.get('content-type'), /^text\/javascript/);
    assert.equal(response.headers.get('cache-control'), 'no-store');
    const body = await response.text();
    assert.match(body, /window\.confirm\(/);
    assert.match(body, /window\.location\.reload\(\)/);
    assert.doesNotMatch(body, /<html/);
  }
});

test('HEAD 只要响应头', async () => {
  const response = await fetch(`${base}/js/362.deadbeef.js`, { method: 'HEAD' });
  assert.equal(response.status, 200);
  assert.match(response.headers.get('content-type'), /^text\/javascript/);
  assert.equal(await response.text(), '');
});

test('其它缺失文件照旧回落成首页：深链接、source map、图标', async () => {
  for (const missing of ['/classification-results/editor', '/js/362.deadbeef.js.map', '/favicon.ico']) {
    const response = await fetch(`${base}${missing}`);
    assert.equal(response.status, 200);
    assert.equal(await response.text(), HOME);
  }
});

test('脚本：第一次问，点确定就刷新', async () => {
  assert.deepEqual(fakePage(workingStorage()).visit(await staleScript()), [1, 1]);
});

test('脚本：刷新之后还是要不到代码块，30 秒内不再问（防止反复弹窗、反复刷新）', async () => {
  const page = fakePage(workingStorage());
  const script = await staleScript();
  assert.deepEqual(page.visit(script), [1, 1]);      // 第一次
  assert.deepEqual(page.visit(script, 2), [1, 1]);    // 2 秒后又来要：不问也不刷新
  assert.deepEqual(page.visit(script, 31), [2, 2]);   // 过了 30 秒再问一次
});

test('脚本：点取消留在本页，不刷新', async () => {
  assert.deepEqual(fakePage(workingStorage()).visit(await staleScript(), 60, false), [1, 0]);
});

test('脚本：用不了会话存储时既不问也不刷新', async () => {
  const broken = { getItem: () => { throw new Error('SecurityError'); }, setItem: () => {} };
  assert.deepEqual(fakePage(broken).visit(await staleScript()), [0, 0]);
});
