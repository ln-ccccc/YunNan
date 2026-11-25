import assert from 'node:assert/strict';
import test from 'node:test';

import { RouteParamError, projectApi } from '../services/projectBackend.js';


test('project backend proxies a validated inference image as binary', async () => {
  const originalFetch = global.fetch;
  const calls = [];
  global.fetch = async (url, options) => {
    calls.push({ url: String(url), options });
    return new Response(Buffer.from('project-image'), {
      status: 200,
      headers: { 'content-type': 'image/png' },
    });
  };

  try {
    const result = await projectApi.getProjectInferenceOutput('7', '101', '101+2022.png', 'session=x');
    assert.equal(result.status, 200);
    assert.equal(result.contentType, 'image/png');
    assert.equal(result.body.toString(), 'project-image');
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, 'http://localhost:5008/api/projects/7/outputs/inference/101/101%2B2022.png');
    assert.equal(calls[0].options.method, 'GET');
    assert.deepEqual(calls[0].options.headers, { cookie: 'session=x', connection: 'close' });   // connection: close 由 fetchBackend 统一追加
    // BFF 出站请求必须带超时，避免上游挂起时无限占用请求线程
    assert.ok(calls[0].options.signal instanceof AbortSignal);
  } finally {
    global.fetch = originalFetch;
  }
});


test('project backend refuses unsafe inference output route segments before fetch', () => {
  assert.throws(
    () => projectApi.getProjectInferenceOutput('../admin', '101', '101+2022.png', 'session=x'),
    /项目参数不合法/,
  );
});

test('deleteProjectDataset sends DELETE to the dataset endpoint with cookie and timeout', async () => {
  const originalFetch = global.fetch;
  const calls = [];
  global.fetch = async (url, options) => {
    calls.push({ url: String(url), options });
    return new Response(JSON.stringify({ success: true, code: 0, data: { id: 9, deleted: true } }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });
  };
  try {
    const result = await projectApi.deleteProjectDataset('7', '9', 'session=x');
    assert.equal(result.status, 200);
    assert.equal(result.body.data.deleted, true);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, 'http://localhost:5008/api/projects/7/datasets/9');
    assert.equal(calls[0].options.method, 'DELETE');
    assert.deepEqual(calls[0].options.headers, { cookie: 'session=x', connection: 'close' });   // connection: close 由 fetchBackend 统一追加
    // BFF 出站请求必须带超时，避免上游挂起时无限占用请求线程
    assert.ok(calls[0].options.signal instanceof AbortSignal);
  } finally {
    global.fetch = originalFetch;
  }
});

test('deleteProjectDataset rejects non-positive route ids before any request', async () => {
  const originalFetch = global.fetch;
  let called = 0;
  global.fetch = async () => { called += 1; throw new Error('should not be called'); };
  try {
    await assert.rejects(async () => projectApi.deleteProjectDataset('7', 'abc', 'session=x'), /数据集参数不合法/);
    await assert.rejects(async () => projectApi.deleteProjectDataset('-1', '9', 'session=x'), /项目参数不合法/);
    assert.equal(called, 0);
  } finally {
    global.fetch = originalFetch;
  }
});

test('route id 校验失败抛类型化 RouteParamError（status=400），带该校验的方法均不触达 fetch', () => {
  const originalFetch = global.fetch;
  let called = 0;
  global.fetch = async () => { called += 1; throw new Error('should not be called'); };
  // 覆盖每个被路由调用的 positiveRouteId 校验点（各取一个非法值）。这些方法都在发请求前
  // 同步抛出；抛普通 Error 时会被路由 catch 兜成 502「上游服务不可用」（2026-11-23 D2）
  const cases = [
    ['deleteProject', () => projectApi.deleteProject('0', 'session=x'), /项目参数不合法/],
    ['deleteProjectDataset 项目', () => projectApi.deleteProjectDataset('../admin', '9', 'session=x'), /项目参数不合法/],
    ['deleteProjectDataset 数据集', () => projectApi.deleteProjectDataset('7', '0', 'session=x'), /数据集参数不合法/],
    ['importBackupManifest', () => projectApi.importBackupManifest('abc', {}, 'session=x'), /项目参数不合法/],
    ['listImageryCandidates', () => projectApi.listImageryCandidates('1.5', 'session=x'), /项目参数不合法/],
    ['imageryPreview', () => projectApi.imageryPreview('-1', 'a.tif', 'session=x'), /项目参数不合法/],
    ['clipImagery', () => projectApi.clipImagery('01', {}, 'session=x'), /项目参数不合法/],
    ['sliceImagery', () => projectApi.sliceImagery('', {}, 'session=x'), /项目参数不合法/],
    ['getProjectOriginalImageryDownload', () => projectApi.getProjectOriginalImageryDownload('7.0', 'job', 'session=x'), /项目参数不合法/],
    ['getProjectInferenceOutput 项目', () => projectApi.getProjectInferenceOutput('7 ', '101', '101+2022.png', 'session=x'), /项目参数不合法/],
    ['getProjectInferenceOutput 矿山', () => projectApi.getProjectInferenceOutput('7', '0', '101+2022.png', 'session=x'), /矿山参数不合法/],
    ['reactivateProjectBasemap 底图', () => projectApi.reactivateProjectBasemap('7', '0', {}, 'session=x'), /底图参数不合法/],
    ['getProjectExportArtifact 导出', () => projectApi.getProjectExportArtifact('7', '0', 'session=x'), /导出参数不合法/],
    ['getProjectBackupManifest 快照', () => projectApi.getProjectBackupManifest('7', '0', 'session=x'), /快照参数不合法/],
  ];
  try {
    for (const [label, invoke, message] of cases) {
      assert.throws(invoke, (error) => {
        assert.ok(error instanceof RouteParamError, `${label} 必须抛 RouteParamError`);
        assert.equal(error.status, 400, `${label} 的 status 必须为 400`);
        assert.match(error.message, message);
        return true;
      }, label);
    }
    assert.equal(called, 0, '校验失败时不得产生任何出站请求');
  } finally {
    global.fetch = originalFetch;
  }
});

test('createProjectExport 出站超时放宽到 10 分钟，且只放宽创建导出（列表查询仍为默认 15s）', async () => {
  const originalFetch = global.fetch;
  const originalTimeout = AbortSignal.timeout;
  const calls = [];
  const timeouts = [];
  global.fetch = async (url, options) => {
    calls.push({ url: String(url), options });
    return new Response(JSON.stringify({ success: true, code: 0, data: { id: 11, format: 'xlsx' } }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });
  };
  // 记录传给 AbortSignal.timeout 的超时值（透传原实现，fetch 仍拿到真实 AbortSignal）
  AbortSignal.timeout = (ms) => { timeouts.push(ms); return originalTimeout(ms); };
  try {
    const result = await projectApi.createProjectExport(10, { format: 'xlsx' }, 'session=x');
    assert.equal(result.status, 200);
    assert.equal(result.body.data.format, 'xlsx');
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, 'http://localhost:5008/api/projects/10/exports');
    assert.equal(calls[0].options.method, 'POST');
    assert.deepEqual(JSON.parse(calls[0].options.body), { format: 'xlsx' });
    // connection: close 由 fetchBackend 统一追加（出站请求不复用连接，见 services/backendFetch.js）
    assert.deepEqual(calls[0].options.headers, { cookie: 'session=x', 'content-type': 'application/json', connection: 'close' });
    assert.ok(calls[0].options.signal instanceof AbortSignal);
    // 后端同步生成（项目 10 的 xlsx 台账实测约 55s）：默认 15s 会让 BFF 先回 502 而后端仍
    // 生成成功，用户重试即重复建档；须与 clip/slice 长请求先例对齐为 10 分钟
    assert.deepEqual(timeouts, [600000]);

    await projectApi.listProjectExports(10, 'session=x');
    assert.deepEqual(timeouts, [600000, 15000], '不得全局放宽：其它请求仍走默认超时');
  } finally {
    AbortSignal.timeout = originalTimeout;
    global.fetch = originalFetch;
  }
});
