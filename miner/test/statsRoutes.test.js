import assert from 'node:assert/strict';
import test from 'node:test';

import express from 'express';

import { createStatsRoutes } from '../routes/stats.js';

async function withServer(handler, requestPath, options = {}) {
  const app = express();
  app.use('/api/stats', handler);
  const server = await new Promise((resolve) => {
    const instance = app.listen(0, () => resolve(instance));
  });
  const { port } = server.address();
  try {
    return await fetch(`http://127.0.0.1:${port}/api/stats${requestPath}`, options);
  } finally {
    await new Promise((resolve, reject) => server.close((err) => (err ? reject(err) : resolve())));
  }
}

test('createStatsRoutes relays overview with session cookie', async () => {
  const calls = [];
  const router = createStatsRoutes({
    projectApi: {
      async request(method, path, { cookie } = {}) {
        calls.push({ method, path, cookie });
        return {
          status: 200,
          body: {
            success: true,
            code: 0,
            data: { project_total: 3, mine_total: 5, feature_total: 12 },
          },
        };
      },
    },
  });
  const response = await withServer(router, '/overview', { headers: { cookie: 'session=abc' } });
  const body = await response.json();
  assert.equal(response.status, 200);
  assert.equal(body.data.project_total, 3);
  assert.deepEqual(calls[0], {
    method: 'GET',
    path: '/api/stats/overview',
    cookie: 'session=abc',
  });
});

test('createStatsRoutes maps upstream failure to 502 without leaking details', async () => {
  const router = createStatsRoutes({
    projectApi: {
      async request() {
        throw new Error('upstream exploded with internal detail');
      },
    },
  });
  const response = await withServer(router, '/overview');
  assert.equal(response.status, 502);
  const body = await response.json();
  assert.equal(body.msg, '上游服务不可用，请稍后重试');
  assert.ok(!JSON.stringify(body).includes('exploded'));
});
