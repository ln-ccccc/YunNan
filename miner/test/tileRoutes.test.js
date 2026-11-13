import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import express from 'express';
import { createTileRoutes } from '../routes/tiles.js';

const minerRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

// 2026-11-14 审查问题29：BFF 瓦片路由是纯传输层——转发 Flask
// /api/projects/<id>/map-resources/<rid>/tiles/<z>/<x>/<y>.png，激活/缺格/
// 透明兜底/缓存头判定全部在后端。测试起一个 mock 上游断言透传语义。

const PNG_BYTES = Buffer.from([0x89, 0x50, 0x4e, 0x47, 1, 2, 3]);

function makeApp(backendBaseUrl) {
  const app = express();
  app.use('/tiles', createTileRoutes({ backendBaseUrl }));
  return app;
}

function listen(app) {
  return new Promise((resolve) => {
    const instance = app.listen(0, () => resolve(instance));
  });
}

/** mock 上游：按 path 前缀映射 {status, contentType, cacheControl, body}。
 * 记录收到的请求（path、cookie）供断言。
 */
function startUpstream(routes) {
  const seen = [];
  const server = http.createServer((req, res) => {
    seen.push({ path: req.url, cookie: req.headers.cookie || '' });
    const route = routes[req.url] || { status: 404, body: '{"code":1}', contentType: 'application/json' };
    if (route.contentType) res.setHeader('Content-Type', route.contentType);
    if (route.cacheControl) res.setHeader('Cache-Control', route.cacheControl);
    res.statusCode = route.status;
    res.end(route.body || '');
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => resolve({ server, seen }));
  });
}

async function get(server, urlPath, headers = {}) {
  const { port } = server.address();
  const response = await fetch(`http://127.0.0.1:${port}${urlPath}`, { headers });
  const body = Buffer.from(await response.arrayBuffer());
  return {
    status: response.status,
    body,
    contentType: response.headers.get('content-type'),
    cacheControl: response.headers.get('cache-control'),
  };
}

test('转发：真实瓦片原样透传（状态/体/Content-Type/Cache-Control）+ 携带 cookie', async () => {
  const upstream = await startUpstream({
    '/api/projects/1/map-resources/2/tiles/9/400/218.png': {
      status: 200, contentType: 'image/png', cacheControl: 'public, max-age=86400', body: PNG_BYTES,
    },
  });
  const server = await listen(makeApp(`http://127.0.0.1:${upstream.server.address().port}`));
  try {
    const r = await get(server, '/tiles/projects/1/2/9/400/218.png', { cookie: 'session=abc' });
    assert.equal(r.status, 200);
    assert.deepEqual([...r.body], [...PNG_BYTES]);
    assert.match(r.contentType || '', /image\/png/);
    assert.match(r.cacheControl || '', /max-age=86400/);
    assert.equal(upstream.seen.length, 1);
    assert.equal(upstream.seen[0].cookie, 'session=abc');
  } finally {
    server.close();
    upstream.server.close();
  }
});

test('转发：上游 404（未激活/缺瓦片）与 200 透明兜底原样透传——BFF 不判断业务状态', async () => {
  const upstream = await startUpstream({
    '/api/projects/1/map-resources/2/tiles/9/400/999.png': {
      status: 200, contentType: 'image/png', cacheControl: 'public, max-age=60', body: PNG_BYTES,
    },
    '/api/projects/1/map-resources/3/tiles/9/400/218.png': {
      status: 404, contentType: 'application/json', body: '{"code":1}',
    },
  });
  const server = await listen(makeApp(`http://127.0.0.1:${upstream.server.address().port}`));
  try {
    const transparent = await get(server, '/tiles/projects/1/2/9/400/999.png');
    assert.equal(transparent.status, 200);
    assert.match(transparent.cacheControl || '', /max-age=60/);
    const notFound = await get(server, '/tiles/projects/1/3/9/400/218.png');
    assert.equal(notFound.status, 404);
  } finally {
    server.close();
    upstream.server.close();
  }
});

test('转发：非数字路径段 → 400 且不打上游', async () => {
  const upstream = await startUpstream({});
  const server = await listen(makeApp(`http://127.0.0.1:${upstream.server.address().port}`));
  try {
    const r = await get(server, '/tiles/projects/abc/2/9/400/218.png');
    assert.equal(r.status, 400);
    const r2 = await get(server, '/tiles/projects/1/2/9/400/21a.png');
    assert.equal(r2.status, 400);
    assert.equal(upstream.seen.length, 0, '非法参数不得到达上游');
  } finally {
    server.close();
    upstream.server.close();
  }
});

test('转发：上游不可达 → 502 JSON', async () => {
  const server = await listen(makeApp('http://127.0.0.1:1'));
  try {
    const r = await get(server, '/tiles/projects/1/2/9/400/218.png');
    assert.equal(r.status, 502);
  } finally {
    server.close();
  }
});

test('守护：tiles.js 不得再接触物理存储（fs/.active/projectStorageRoot 零引用）', () => {
  const source = fs.readFileSync(path.join(minerRoot, 'routes', 'tiles.js'), 'utf-8');
  // 只断言代码：剥离 // 行注释与块注释后再匹配，避免注释里的说明文字误触发
  const code = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  assert.ok(!/from 'fs'|require\('fs'\)/.test(code), '不得 import fs');
  assert.ok(!code.includes('.active'), '不得读 .active 激活标记');
  assert.ok(!code.includes('projectStorageRoot'), '不得依赖物理存储根');
  assert.ok(!code.includes('TRANSPARENT_TILE_PNG'), '透明兜底语义归后端，BFF 不内置');
});

test('守护：server.js 不得再解析 PROJECT_STORAGE_ROOT', () => {
  const source = fs.readFileSync(path.join(minerRoot, 'server.js'), 'utf-8');
  assert.ok(!source.includes('PROJECT_STORAGE_ROOT'), 'BFF 不再配置物理存储根');
});
