import assert from 'node:assert/strict';
import test from 'node:test';
import express from 'express';

import { createProjectRoutes } from '../routes/projects.js';
import { RouteParamError } from '../services/projectBackend.js';

// 参数校验错误被兜成 502 的回归用例（2026-11-23 D2）。
// 单独成文件的原因：projectRoutes.test.js 含字面 NUL 字节（'II*\x00fake-tif-bytes'），
// 被 git 视为二进制且整文件为 CRLF，改动无法在 diff 中评审。夹具与该文件同构。

// 必须在替换 global.fetch 之前捕获：测试自身访问本地 express 服务始终用真实 fetch
const realFetch = global.fetch;
const JOB_ID = '38d04109-6dda-4a0e-aabb-ccddeeff0011';
const GATEWAY_ERROR_BODY = { success: false, code: 1, msg: '上游服务不可用，请稍后重试' };
// BFF 出站地址（projectBackend.js 的默认 GEOVIEW_BACKEND_URL）
const UPSTREAM_ORIGIN = (process.env.GEOVIEW_BACKEND_URL || 'http://localhost:5008').replace(/\/$/, '');

async function withServer(handler, requestPath = '', options = {}) {
  const app = express();
  app.use(express.json({ limit: '20mb' }));
  app.use('/api/projects', handler);
  const server = await new Promise((resolve) => {
    const instance = app.listen(0, () => resolve(instance));
  });
  const { port } = server.address();
  try {
    return await realFetch(`http://127.0.0.1:${port}/api/projects${requestPath}`, options);
  } finally {
    await new Promise((resolve, reject) => server.close((err) => (err ? reject(err) : resolve())));
  }
}

const deleteRequest = { method: 'DELETE', headers: { cookie: 'session=x' } };
const postJson = { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' };

// 假 projectApi：记录每个被调用的方法名与入参；overrides 里的方法按给定实现执行
function fakeProjectApi(overrides = {}) {
  const calls = [];
  const projectApi = new Proxy({}, {
    get(_target, property) {
      return (...args) => {
        calls.push({ method: String(property), args });
        if (Object.hasOwn(overrides, property)) return overrides[property](...args);
        return Promise.resolve({ status: 200, body: { success: true, code: 0, data: {} } });
      };
    },
  });
  return { projectApi, calls };
}

// 临时接管 console.error：502 路径会打印上游异常，既要断言它被调用，也避免污染测试输出
async function captureConsoleError(run) {
  const original = console.error;
  const calls = [];
  console.error = (...args) => { calls.push(args); };
  try {
    return await run(calls);
  } finally {
    console.error = original;
  }
}

// 真实 projectApi + 假上游：只拦截 BFF 出站请求（发往 UPSTREAM_ORIGIN），记录后按 respond
// 应答；绝不触达真实后端
function stubUpstreamFetch(respond = () => new Response(
  JSON.stringify({ success: true, code: 0, data: { upstream: true } }),
  { status: 200, headers: { 'content-type': 'application/json' } },
)) {
  const original = global.fetch;
  const calls = [];
  global.fetch = async (url, options) => {
    calls.push({ method: options?.method, url: String(url) });
    return respond(url, options);
  };
  return { calls, restore() { global.fetch = original; } };
}

test('删除数据集：非法 project id → 400「项目参数不合法」，projectApi 完全未被调用', async () => {
  const { projectApi, calls } = fakeProjectApi();
  const router = createProjectRoutes({ projectApi });

  for (const requestPath of ['/abc/datasets/9', '/0/datasets/9', '/-1/datasets/9', '/1.5/datasets/9']) {
    const response = await withServer(router, requestPath, deleteRequest);
    assert.equal(response.status, 400, requestPath);
    assert.deepEqual(await response.json(), { success: false, code: 1, msg: '项目参数不合法' });
  }

  assert.deepEqual(calls, [], '非法 project id 在路由参数守卫处即被拒绝，不得触达 projectApi');
});

test('删除数据集：非法 dataset id → 400「数据集参数不合法」，不再兜成 502 也不记上游错误日志', async () => {
  const { projectApi, calls } = fakeProjectApi({
    deleteProjectDataset() {
      // 与真实 service 的 positiveRouteId 行为一致：发请求前抛出类型化校验错误
      throw new RouteParamError('数据集参数不合法');
    },
  });
  const router = createProjectRoutes({ projectApi });
  const invalidIds = ['0', 'abc', '-1', '07', '1.5'];

  await captureConsoleError(async (logs) => {
    for (const datasetId of invalidIds) {
      const response = await withServer(router, `/7/datasets/${datasetId}`, deleteRequest);
      assert.equal(response.status, 400, `datasetId=${datasetId}`);
      assert.deepEqual(await response.json(), { success: false, code: 1, msg: '数据集参数不合法' });
    }
    // 只调用了 deleteProjectDataset 本身；校验由 service 完成，没有回退到其它上游方法
    assert.deepEqual(calls, invalidIds.map((datasetId) => ({
      method: 'deleteProjectDataset',
      args: ['7', datasetId, 'session=x'],
    })));
    assert.deepEqual(logs, [], '参数校验失败不是上游异常，不得按 upstream error 记日志');
  });
});

test('删除数据集：非法 dataset id 不产生任何 BFF 出站请求（真实 service + 假上游）', async () => {
  const upstream = stubUpstreamFetch();
  // 不注入 projectApi：走真实 service，验证 positiveRouteId → RouteParamError → 400 的完整链路
  const router = createProjectRoutes();
  try {
    await captureConsoleError(async (logs) => {
      for (const datasetId of ['0', 'abc']) {
        const response = await withServer(router, `/7/datasets/${datasetId}`, deleteRequest);
        assert.equal(response.status, 400, `datasetId=${datasetId}`);
        assert.deepEqual(await response.json(), { success: false, code: 1, msg: '数据集参数不合法' });
      }
      assert.deepEqual(upstream.calls, [], '非法 dataset id 不得产生任何 BFF 出站请求');
      assert.deepEqual(logs, []);
    });
  } finally {
    upstream.restore();
  }
});

test('删除数据集：合法 id → 原样透传 project id / dataset id / cookie 与上游状态、响应体', async () => {
  const calls = [];
  const upstreamBody = { success: false, code: 1, msg: '数据集不存在' };
  const router = createProjectRoutes({
    projectApi: {
      async deleteProjectDataset(projectId, datasetId, cookie) {
        calls.push({ projectId, datasetId, cookie });
        return { status: 404, body: upstreamBody };
      },
    },
  });

  const response = await withServer(router, '/7/datasets/9', {
    method: 'DELETE',
    headers: { cookie: 'session=route-test' },
  });
  assert.equal(response.status, 404);
  assert.deepEqual(await response.json(), upstreamBody);
  assert.deepEqual(calls, [{ projectId: '7', datasetId: '9', cookie: 'session=route-test' }]);

  // 真实 service：合法 id 恰好产生一次发往数据集端点的 DELETE，状态与响应体原样回给浏览器
  const upstream = stubUpstreamFetch();
  try {
    const relayed = await withServer(createProjectRoutes(), '/7/datasets/9', deleteRequest);
    assert.equal(relayed.status, 200);
    assert.deepEqual(await relayed.json(), { success: true, code: 0, data: { upstream: true } });
    assert.deepEqual(upstream.calls, [{
      method: 'DELETE',
      url: `${UPSTREAM_ORIGIN}/api/projects/7/datasets/9`,
    }]);
  } finally {
    upstream.restore();
  }
});

test('删除项目（deleteProject 同样带 positiveRouteId）：非法 id → 400 且不触达 projectApi，合法 id → 透传', async () => {
  const { projectApi, calls } = fakeProjectApi();
  const router = createProjectRoutes({ projectApi });

  for (const requestPath of ['/abc', '/0', '/-1']) {
    const response = await withServer(router, requestPath, deleteRequest);
    assert.equal(response.status, 400, requestPath);
    assert.deepEqual(await response.json(), { success: false, code: 1, msg: '项目参数不合法' });
  }
  assert.deepEqual(calls, []);

  const deleted = await withServer(router, '/7', deleteRequest);
  assert.equal(deleted.status, 200);
  assert.deepEqual(calls, [{ method: 'deleteProject', args: ['7', 'session=x'] }]);
});

test('所有经 positiveRouteId 校验的路由：非法 id 一律 400 且不向上游发请求', async () => {
  const upstream = stubUpstreamFetch();
  const router = createProjectRoutes();
  // 守卫位置不同，响应信封也不同：router.param 与 service 校验回 { success, code, msg }，
  // 路由内的内联守卫回 { error }
  const cases = [
    ['DELETE', '/abc', '项目参数不合法'],
    ['DELETE', '/0', '项目参数不合法'],
    ['DELETE', '/7/datasets/abc', '数据集参数不合法'],
    ['POST', '/abc/backups/import', '项目参数不合法'],
    ['GET', '/0/imagery/candidates', '项目参数不合法'],
    ['GET', '/-1/imagery/preview?source=a.tif', '项目参数不合法'],
    ['POST', '/1.5/imagery/clip', '项目参数不合法'],
    ['POST', '/01/imagery/slice', '项目参数不合法'],
    ['GET', `/abc/mines/original-imagery/${JOB_ID}/download`, '项目参数不合法'],
    ['GET', '/1/mines/original-imagery/not-a-uuid/download', '项目参数不合法'],
    ['GET', '/abc/outputs/inference/101/101+2022.png', '项目参数不合法'],
    ['GET', '/1/outputs/inference/0/101+2022.png', 'Invalid project output path'],
    ['GET', '/1/exports/0/artifact', 'Invalid export path'],
    ['GET', '/1/backups/0/manifest', '项目参数不合法'],
    ['POST', '/1/spatial/basemaps/0/reactivate', 'Invalid basemap path'],
  ];
  try {
    await captureConsoleError(async (logs) => {
      for (const [method, requestPath, expected] of cases) {
        upstream.calls.length = 0;
        const response = await withServer(router, requestPath, {
          POST: postJson,
          DELETE: deleteRequest,
          GET: {},
        }[method]);
        const label = `${method} ${requestPath}`;
        assert.equal(response.status, 400, label);
        const body = await response.json();
        assert.equal(body.msg ?? body.error, expected, label);
        assert.deepEqual(upstream.calls, [], `${label} 不得向上游发请求`);
      }
      assert.deepEqual(logs, [], '参数校验失败不得记成上游错误');
    });
  } finally {
    upstream.restore();
  }
});

test('非参数错误口径不变：普通异常、带 5xx 或非整数 status 的异常仍是 502 + 固定文案，细节只进日志', async () => {
  const detail = 'connect ECONNREFUSED 10.0.0.5:5008 /internal/path';
  const thrownErrors = [
    ['普通 Error', new Error(detail)],
    ['TypeError + cause', new TypeError('fetch failed', { cause: Object.assign(new Error(detail), { code: 'ECONNREFUSED' }) })],
    ['status=503', Object.assign(new Error(detail), { status: 503 })],
    ['status=500', Object.assign(new Error(detail), { status: 500 })],
    ['status="400"（字符串）', Object.assign(new Error(detail), { status: '400' })],
    ['status=400.5（非整数）', Object.assign(new Error(detail), { status: 400.5 })],
    ['status=399', Object.assign(new Error(detail), { status: 399 })],
  ];

  for (const [label, thrown] of thrownErrors) {
    const router = createProjectRoutes({
      projectApi: { deleteProjectDataset() { throw thrown; } },
    });
    await captureConsoleError(async (logs) => {
      const response = await withServer(router, '/7/datasets/9', deleteRequest);
      assert.equal(response.status, 502, label);
      assert.deepEqual(await response.json(), GATEWAY_ERROR_BODY, label);
      assert.deepEqual(logs, [['projects route upstream error:', thrown]], `${label} 的细节只进日志`);
    });
  }
});

test('上游超时与连接失败（真实 service）：仍是 502 + 固定文案，不回显异常细节', async () => {
  const failures = [
    ['AbortSignal.timeout 触发', () => { throw new DOMException('The operation was aborted due to timeout', 'TimeoutError'); }],
    ['连接被拒', () => {
      throw new TypeError('fetch failed', { cause: Object.assign(new Error('connect ECONNREFUSED 127.0.0.1:5008'), { code: 'ECONNREFUSED' }) });
    }],
  ];

  for (const [label, respond] of failures) {
    const upstream = stubUpstreamFetch(respond);
    const router = createProjectRoutes();
    try {
      await captureConsoleError(async (logs) => {
        const response = await withServer(router, '/7/datasets/9', deleteRequest);
        assert.equal(response.status, 502, label);
        assert.deepEqual(await response.json(), GATEWAY_ERROR_BODY, label);
        assert.equal(logs.length, 1, label);
        assert.equal(logs[0][0], 'projects route upstream error:');
        assert.equal(logs[0][1].name, label === 'AbortSignal.timeout 触发' ? 'TimeoutError' : 'TypeError');
        assert.equal(upstream.calls.length, 1, label);
      });
    } finally {
      upstream.restore();
    }
  }
});

test('带其它 4xx status 的类型化错误按其状态码返回（relay 工厂路由同样生效）', async () => {
  const router = createProjectRoutes({
    projectApi: {
      request() {
        throw Object.assign(new RouteParamError('项目参数不合法'), { status: 422 });
      },
    },
  });

  const response = await withServer(router, '/7/spatial');
  assert.equal(response.status, 422);
  assert.deepEqual(await response.json(), { success: false, code: 1, msg: '项目参数不合法' });
});

test('relay 工厂路由的 catch 同样识别参数校验错误：RouteParamError → 400，不再兜成 502', async () => {
  const { projectApi } = fakeProjectApi({
    request() { throw new RouteParamError('项目参数不合法'); },
  });
  const router = createProjectRoutes({ projectApi });

  await captureConsoleError(async (logs) => {
    const response = await withServer(router, '/7/spatial');
    assert.equal(response.status, 400);
    assert.deepEqual(await response.json(), { success: false, code: 1, msg: '项目参数不合法' });
    assert.deepEqual(logs, []);
  });
});
