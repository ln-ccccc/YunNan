import assert from 'node:assert/strict';
import test from 'node:test';
import express from 'express';

import { createInterpretationRoutes } from '../routes/interpretation.js';

async function withServer(router, requestPath) {
  const app = express();
  app.use('/api/interpretation', router);
  const server = await new Promise((resolve) => {
    const instance = app.listen(0, () => resolve(instance));
  });
  const { port } = server.address();
  try {
    return await fetch(`http://127.0.0.1:${port}/api/interpretation${requestPath}`);
  } finally {
    await new Promise((resolve, reject) => server.close((err) => (err ? reject(err) : resolve())));
  }
}

test('kml-roi-history：project_id/page/limit 透传到后端查询串并原样返回', async () => {
  const calls = [];
  const router = createInterpretationRoutes({
    projectApi: {
      async request(method, path) {
        calls.push({ method, path });
        return {
          status: 200,
          body: { code: 0, msg: 'success', data: [{ id: 1, record_id: 'r1' }], count: 1 },
        };
      },
    },
  });

  const res = await withServer(router, '/kml-roi-history?project_id=1&page=2&limit=50');
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.data.length, 1);
  assert.deepEqual(calls, [{
    method: 'GET',
    path: '/api/analysis/kml_roi_history?page=2&limit=50&project_id=1',
  }]);
});

test('kml-roi-history：page/limit 缺省时使用默认值', async () => {
  const calls = [];
  const router = createInterpretationRoutes({
    projectApi: {
      async request(method, path) {
        calls.push(path);
        return { status: 200, body: { code: 0, data: [], count: 0 } };
      },
    },
  });

  await withServer(router, '/kml-roi-history?project_id=3');
  assert.equal(calls[0], '/api/analysis/kml_roi_history?page=1&limit=20&project_id=3');
});

test('kml-roi-history：project_id 缺失或非法一律 400 且不请求上游', async () => {
  const calls = [];
  const router = createInterpretationRoutes({
    projectApi: {
      async request(method, path) {
        calls.push(path);
        return { status: 200, body: {} };
      },
    },
  });

  for (const query of ['', '?page=1&limit=20', '?project_id=0', '?project_id=-1', '?project_id=abc']) {
    const res = await withServer(router, `/kml-roi-history${query}`);
    assert.equal(res.status, 400, `query=${query}`);
  }
  assert.deepEqual(calls, []);
});

test('kml-roi-history：上游非 200 原样透传不塑形', async () => {
  const router = createInterpretationRoutes({
    projectApi: {
      async request() {
        return { status: 502, body: { success: false, code: 1, msg: '上游异常' } };
      },
    },
  });

  const res = await withServer(router, '/kml-roi-history?project_id=1');
  assert.equal(res.status, 502);
  const body = await res.json();
  assert.equal(body.msg, '上游异常');
});

test('kml-roi-history：BFF 纯透传原序（展示排序已移前端 M3）', async () => {
  // 排序契约已移至前端 utils/interpretationOrdering.js（interpretationOrdering.test.js 守护）
});
