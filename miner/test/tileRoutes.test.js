import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import express from 'express';
import { createTileRoutes } from '../routes/tiles.js';

function makeApp(root) {
  const app = express();
  app.use('/tiles', createTileRoutes({ projectStorageRoot: root }));
  return app;
}

function listen(app) {
  return new Promise((resolve) => {
    const instance = app.listen(0, () => resolve(instance));
  });
}

async function get(server, urlPath) {
  const { port } = server.address();
  const response = await fetch(`http://127.0.0.1:${port}${urlPath}`);
  const body = Buffer.from(await response.arrayBuffer());
  return { status: response.status, body, cacheControl: response.headers.get('cache-control') };
}

test('项目瓦片路由：存在瓦片原样返回', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'tiles-'));
  const xDir = path.join(root, 'projects', '1', 'tiles', '2', '9', '400');
  fs.mkdirSync(xDir, { recursive: true });
  fs.writeFileSync(path.join(xDir, '218.png'), Buffer.from([0x89, 0x50, 0x4e, 0x47, 1, 2, 3]));
  fs.writeFileSync(path.join(root, 'projects', '1', 'tiles', '2', '.active'), '');
  const server = await listen(makeApp(root));
  try {
    const r = await get(server, '/tiles/projects/1/2/9/400/218.png');
    assert.equal(r.status, 200);
    assert.deepEqual([...r.body.subarray(0, 4)], [0x89, 0x50, 0x4e, 0x47]);
    assert.equal(r.body[4], 1);
  } finally {
    server.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('项目瓦片路由：锯齿边缺瓦片但 zoom 目录存在 → 200 透明 PNG + 缓存头', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'tiles-'));
  const zoomDir = path.join(root, 'projects', '1', 'tiles', '2', '9', '400');
  fs.mkdirSync(zoomDir, { recursive: true });
  fs.writeFileSync(path.join(zoomDir, '217.png'), Buffer.from('png'));
  fs.writeFileSync(path.join(root, 'projects', '1', 'tiles', '2', '.active'), '');
  const server = await listen(makeApp(root));
  try {
    const r = await get(server, '/tiles/projects/1/2/9/400/999.png');
    assert.equal(r.status, 200);
    assert.deepEqual([...r.body.subarray(0, 4)], [0x89, 0x50, 0x4e, 0x47]);
    // 透明兜底只短缓存（2026-11-05 P2-1：钉 24h 会让重切后旧浏览器透明一天）
    assert.match(r.cacheControl || '', /max-age=60/);
  } finally {
    server.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('项目瓦片路由：zoom 目录整体缺失 → 404（资源级异常不掩盖）', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'tiles-'));
  fs.mkdirSync(path.join(root, 'projects', '1', 'tiles', '2'), { recursive: true });
  fs.writeFileSync(path.join(root, 'projects', '1', 'tiles', '2', '.active'), '');
  const server = await listen(makeApp(root));
  try {
    const r = await get(server, '/tiles/projects/1/2/12/3200/1700.png');
    assert.equal(r.status, 404);
  } finally {
    server.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('项目瓦片路由：.active 缺失 → 404', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'tiles-'));
  const zoomDir = path.join(root, 'projects', '1', 'tiles', '2', '9', '400');
  fs.mkdirSync(zoomDir, { recursive: true });
  const server = await listen(makeApp(root));
  try {
    const r = await get(server, '/tiles/projects/1/2/9/400/218.png');
    assert.equal(r.status, 404);
  } finally {
    server.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('项目瓦片路由：非数字路径段 → 400', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'tiles-'));
  const server = await listen(makeApp(root));
  try {
    const r2 = await get(server, '/tiles/projects/abc/2/9/400/218.png');
    assert.equal(r2.status, 400);
  } finally {
    server.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});
