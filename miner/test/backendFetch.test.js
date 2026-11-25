import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import net from 'node:net';
import { fileURLToPath } from 'node:url';

import { fetchBackend } from '../services/backendFetch.js';

// 照 gunicorn 行为的模拟服务（原始 TCP，不经 Node 的 http 服务——它会自动发 Keep-Alive 超时提示，
// 线上的 gunicorn 不发）：响应声明 keep-alive，连接空闲满 idleMs 即关闭。线上 idle 是 2 秒，
// undici 的连接池默认留 4 秒，差出的这 2 秒就是竞态窗口。
function startGunicornMimic({ idleMs = 2000 } = {}) {
  const body = '{"ok":true}';
  const response = `HTTP/1.1 200 OK\r\nServer: gunicorn\r\nConnection: keep-alive\r\n`
    + `Content-Type: application/json\r\nContent-Length: ${Buffer.byteLength(body)}\r\n\r\n${body}`;
  const state = { connections: 0, requestHeads: [] };
  const sockets = new Set();
  const server = net.createServer((socket) => {
    state.connections += 1;
    sockets.add(socket);
    socket.on('close', () => sockets.delete(socket));
    let idleTimer = setTimeout(() => socket.destroy(), idleMs);
    socket.on('data', (chunk) => {
      clearTimeout(idleTimer);
      state.requestHeads.push(String(chunk).split('\r\n\r\n')[0]);
      socket.write(response);
      idleTimer = setTimeout(() => socket.destroy(), idleMs);
    });
    socket.on('error', () => {});
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => resolve({
      url: `http://127.0.0.1:${server.address().port}/anything`,
      state,
      close: () => {
        for (const socket of sockets) socket.destroy();
        return new Promise((done) => server.close(done));
      },
    }));
  });
}

const getFive = async (request) => {
  const statuses = [];
  for (let index = 0; index < 5; index += 1) {
    const response = await request();
    statuses.push(response.status);
    await response.text();   // 读完响应体，连接才有机会被归还连接池
  }
  return statuses;
};

test('fetchBackend 每个请求都新建连接：不复用服务端可能正在关闭的连接', async () => {
  const mimic = await startGunicornMimic();
  try {
    assert.deepEqual(await getFive(() => fetchBackend(mimic.url)), [200, 200, 200, 200, 200]);
    assert.equal(mimic.state.requestHeads.length, 5);
    for (const head of mimic.state.requestHeads) {
      assert.match(head, /\r\nconnection: close\r?\n/i, '每个出站请求都要声明 Connection: close');
    }
    assert.equal(mimic.state.connections, 5, '5 个请求对应 5 条连接');
  } finally {
    await mimic.close();
  }
});

test('对照组：直接用全局 fetch 时连接会被复用（这正是竞态的前提，也是上一条测试有区分力的证明）', async () => {
  const mimic = await startGunicornMimic();
  try {
    await getFive(() => fetch(mimic.url));
    assert.ok(mimic.state.connections < 5, `全局 fetch 应复用连接，实际建了 ${mimic.state.connections} 条——若复用不再发生，上一条测试就失去了对照意义`);
    for (const head of mimic.state.requestHeads) {
      assert.doesNotMatch(head, /\r\nconnection: close\r?\n/i);
    }
  } finally {
    await mimic.close();
  }
});

test('调用方传入的请求头不能覆盖这条策略，其余选项原样透传', async () => {
  const originalFetch = globalThis.fetch;
  const seen = [];
  const signal = new AbortController().signal;
  globalThis.fetch = async (url, options) => {
    seen.push({ url, options });
    return new Response('{}', { status: 200 });
  };
  try {
    await fetchBackend('http://backend.test/api/x', {
      method: 'POST',
      headers: { 'content-type': 'application/json', connection: 'keep-alive', cookie: 'session=abc' },
      body: '{}',
      signal,
    });
    assert.equal(seen.length, 1);
    assert.equal(seen[0].url, 'http://backend.test/api/x');
    assert.deepEqual(seen[0].options.headers, { 'content-type': 'application/json', cookie: 'session=abc', connection: 'close' });
    assert.equal(seen[0].options.method, 'POST');
    assert.equal(seen[0].options.body, '{}');
    assert.equal(seen[0].options.signal, signal);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('BFF 访问后端只经 fetchBackend（服务端、路由层不得直接 fetch）', () => {
  // 规则只留一处的另一半。2026-11-24 之前会话校验与接口转发各自 fetch，竞态两边各中招
  // （线上 9 次 + 29 次），谁都没管。
  const root = fileURLToPath(new URL('../', import.meta.url));
  const helper = 'services' + '/' + 'backendFetch.js';
  const offenders = [];
  const visit = (relative) => {
    const full = `${root}${relative}`;
    for (const entry of fs.readdirSync(full, { withFileTypes: true })) {
      const child = `${relative}/${entry.name}`;
      if (entry.isDirectory()) { visit(child); continue; }
      if (!entry.name.endsWith('.js')) continue;
      fs.readFileSync(full + '/' + entry.name, 'utf8').split(/\r?\n/).forEach((line, index) => {
        if (/\bfetch\(/.test(line)) offenders.push(`${child}:${index + 1} ${line.trim().slice(0, 80)}`);
      });
    }
  };
  for (const directory of ['services', 'routes']) visit(directory);
  for (const file of ['server.js']) {
    fs.readFileSync(`${root}${file}`, 'utf8').split(/\r?\n/).forEach((line, index) => {
      if (/\bfetch\(/.test(line)) offenders.push(`${file}:${index + 1} ${line.trim().slice(0, 80)}`);
    });
  }
  assert.deepEqual(offenders.filter((line) => !line.startsWith(`${helper}:`)), [], '出站请求必须走 fetchBackend');
});
