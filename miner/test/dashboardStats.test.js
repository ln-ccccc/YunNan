import assert from 'node:assert/strict';
import test from 'node:test';
import express from 'express';

import { aggregateLandTypeList } from '../services/dashboardStats.js';
import { createProjectRoutes } from '../routes/projects.js';

async function withServer(handler, requestPath = '') {
  const app = express();
  app.use('/api/projects', handler);
  const server = await new Promise((resolve) => {
    const instance = app.listen(0, () => resolve(instance));
  });
  const { port } = server.address();
  try {
    return await fetch(`http://127.0.0.1:${port}/api/projects${requestPath}`);
  } finally {
    await new Promise((resolve, reject) => server.close((err) => (err ? reject(err) : resolve())));
  }
}

test('归类聚合：组合地类拆分计数，降序排列，未知桶固定末位', () => {
  const rows = aggregateLandTypeList([
    { name: '林地,草地', value: 261 },
    { name: '草地', value: 87 },
    { name: '林地', value: 72 },
    { name: '未知', value: 71 },
    { name: '耕地,草地', value: 5 },
  ]);
  assert.deepEqual(rows, [
    { name: '草地', value: 353 },
    { name: '林地', value: 333 },
    { name: '耕地', value: 5 },
    { name: '未知', value: 71 },
  ]);
});

test('归类聚合：空数据与非数组入参一律返回空数组', () => {
  assert.deepEqual(aggregateLandTypeList([]), []);
  assert.deepEqual(aggregateLandTypeList(null), []);
  assert.deepEqual(aggregateLandTypeList(undefined), []);
  assert.deepEqual(aggregateLandTypeList('耕地,草地'), []);
  assert.deepEqual(aggregateLandTypeList({ name: '耕地', value: 3 }), []);
});

test('归类聚合：非法数值项整项跳过，无名桶归入未知，重复地类只计一次', () => {
  const rows = aggregateLandTypeList([
    { name: '林地,林地', value: 3 },
    { name: '耕地', value: 0 },
    { name: '园地', value: -2 },
    { name: '草地', value: Number('not-a-number') },
    { name: '', value: 4 },
  ]);
  assert.deepEqual(rows, [
    { name: '林地', value: 3 },
    { name: '未知', value: 4 },
  ]);
});

test('归类聚合：全未知输入只产生一个未知桶，全角与空格分隔符同样拆分', () => {
  assert.deepEqual(
    aggregateLandTypeList([
      { name: '未知', value: 10 },
      { name: '未标注', value: 2 },
    ]),
    [{ name: '未知', value: 12 }],
  );
  // 同分时按 zh-CN 拼音序（耕 geng < 园 yuan）
  assert.deepEqual(
    aggregateLandTypeList([
      { name: '耕地，园地', value: 4 },
      { name: '耕地 园地', value: 1 },
    ]),
    [
      { name: '耕地', value: 5 },
      { name: '园地', value: 5 },
    ],
  );
});

test('stats 路由：landTypeList 在 BFF 归类后下发，其余字段原样透传', async () => {
  const calls = [];
  const router = createProjectRoutes({
    projectApi: {
      async request(method, path) {
        calls.push({ method, path });
        return {
          status: 200,
          body: {
            success: true,
            code: 0,
            data: {
              mineTotal: 565,
              landTypeList: [
                { name: '林地,草地', value: 261 },
                { name: '未知', value: 71 },
              ],
            },
          },
        };
      },
    },
  });

  const res = await withServer(router, '/1/stats');
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.deepEqual(body.data.landTypeList, [
    { name: '草地', value: 261 },
    { name: '林地', value: 261 },
    { name: '未知', value: 71 },
  ]);
  assert.equal(body.data.mineTotal, 565);
  assert.deepEqual(calls, [{ method: 'GET', path: '/api/projects/1/stats' }]);
});

test('stats 路由：上游非 200 时不塑造、原样透传', async () => {
  const router = createProjectRoutes({
    projectApi: {
      async request() {
        return { status: 502, body: { success: false, code: 1, msg: '上游异常' } };
      },
    },
  });

  const res = await withServer(router, '/1/stats');
  assert.equal(res.status, 502);
  const body = await res.json();
  assert.equal(body.msg, '上游异常');
  assert.equal('landTypeList' in body, false);
});
