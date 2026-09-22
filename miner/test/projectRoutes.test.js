import assert from 'node:assert/strict';
import test from 'node:test';
import express from 'express';

import { createProjectRoutes } from '../routes/projects.js';

async function withServer(handler, requestPath = '', options = {}) {
  const app = express();
  app.use(express.json({ limit: '20mb' }));
  app.use('/api/projects', handler);
  const server = await new Promise((resolve) => {
    const instance = app.listen(0, () => resolve(instance));
  });
  const { port } = server.address();
  try {
    return await fetch(`http://127.0.0.1:${port}/api/projects${requestPath}`, options);
  } finally {
    await new Promise((resolve, reject) => server.close((err) => (err ? reject(err) : resolve())));
  }
}

test('createProjectRoutes proxies list requests to backend client', async () => {
  const calls = [];
  const router = createProjectRoutes({
    projectApi: {
      async listProjects(query) {
        calls.push(query);
        return {
          status: 200,
          body: { success: true, code: 0, data: { items: [{ id: 1, name: '项目A' }], count: 1 } },
        };
      },
    },
  });

  const app = express();
  app.use('/api/projects', router);
  const server = await new Promise((resolve) => {
    const instance = app.listen(0, () => resolve(instance));
  });
  const { port } = server.address();
  try {
    const response = await fetch(`http://127.0.0.1:${port}/api/projects?name=项目A&status=active`);
    const body = await response.json();
    assert.equal(response.status, 200);
    assert.equal(body.data.count, 1);
    assert.deepEqual(calls[0], { name: '项目A', region: null, status: 'active', monitor_year: null });
  } finally {
    await new Promise((resolve, reject) => server.close((err) => (err ? reject(err) : resolve())));
  }
});

test('createProjectRoutes transparently proxies project overview id, cookie, status, and body', async () => {
  const calls = [];
  const upstreamBody = {
    success: true,
    code: 0,
    data: { project_id: 42, readiness: { status: 'partial' } },
  };
  const router = createProjectRoutes({
    projectApi: {
      async getProjectOverview(projectId, cookie) {
        calls.push({ projectId, cookie });
        return { status: 200, body: upstreamBody };
      },
    },
  });

  const response = await withServer(router, '/42/overview', {
    headers: { cookie: 'admin_user_id=7' },
  });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), upstreamBody);
  assert.deepEqual(calls, [{ projectId: '42', cookie: 'admin_user_id=7' }]);
});

test('createProjectRoutes transparently proxies project asset filters without rewriting the query', async () => {
  const calls = [];
  const upstreamBody = {
    success: true,
    code: 0,
    data: { items: [{ id: 'imagery:201', status: 'failed' }], count: 1 },
  };
  const router = createProjectRoutes({
    projectApi: {
      async listProjectAssets(projectId, query, cookie) {
        calls.push({ projectId, query, cookie });
        return { status: 200, body: upstreamBody };
      },
    },
  });

  const response = await withServer(router, '/42/assets?type=imagery&status=failed', {
    headers: { cookie: 'admin_user_id=7' },
  });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), upstreamBody);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].projectId, '42');
  assert.equal(calls[0].query.type, 'imagery');
  assert.equal(calls[0].query.status, 'failed');
  assert.deepEqual(Object.keys(calls[0].query), ['type', 'status']);
  assert.equal(calls[0].cookie, 'admin_user_id=7');
});

test('createProjectRoutes rejects encoded project traversal before any backend call', async () => {
  const calls = [];
  const projectApi = new Proxy({}, {
    get(_target, property) {
      calls.push(String(property));
      return async () => ({ status: 200, body: { success: true, code: 0, data: {} } });
    },
  });
  const router = createProjectRoutes({ projectApi });
  const invalidBody = { success: false, code: 1, msg: '项目参数不合法' };

  for (const requestPath of [
    '/%2e%2e%2fadmin/overview',
    '/%2e%2e%2fadmin/assets',
  ]) {
    const response = await withServer(router, requestPath, {
      headers: { cookie: 'admin_user_id=7' },
    });
    assert.equal(response.status, 400);
    assert.deepEqual(await response.json(), invalidBody);
  }

  assert.deepEqual(calls, []);
});

test('createProjectRoutes rejects non-positive job and backup identifiers before any backend call', async () => {
  const calls = [];
  const projectApi = new Proxy({}, {
    get(_target, property) {
      calls.push(String(property));
      return async () => ({ status: 200, body: { success: true, code: 0, data: {} } });
    },
  });
  const router = createProjectRoutes({ projectApi });
  const invalidBody = { success: false, code: 1, msg: '项目参数不合法' };

  for (const requestPath of [
    '/42/spatial/jobs/0/retry',
    '/42/backups/0/restore',
  ]) {
    const response = await withServer(router, requestPath, {
      method: 'POST',
      headers: { cookie: 'admin_user_id=7', 'content-type': 'application/json' },
      body: '{}',
    });
    assert.equal(response.status, 400);
    assert.deepEqual(await response.json(), invalidBody);
  }

  assert.deepEqual(calls, []);
});

test('createProjectRoutes accepts a spatial job UUID and forwards it unchanged', async () => {
  const calls = [];
  const jobId = 'dce7d49f-0e48-4e4d-a29c-5f0b6356e8fe';
  const router = createProjectRoutes({
    projectApi: {
      async request(method, path, options) {
        calls.push({ method, path, options });
        return { status: 200, body: { success: true, code: 0, data: { id: jobId } } };
      },
    },
  });

  const response = await withServer(router, `/42/spatial/jobs/${jobId}/retry`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: '{}',
  });

  assert.equal(response.status, 200);
  assert.deepEqual(calls, [{
    method: 'POST',
    path: `/api/projects/42/spatial/jobs/${jobId}/retry`,
    options: { body: {}, cookie: '' },
  }]);
});

test('createProjectRoutes relays upstream overview 404 status and body unchanged', async () => {
  const upstreamBody = { success: false, code: 1, msg: '项目不存在' };
  const router = createProjectRoutes({
    projectApi: {
      async getProjectOverview() {
        return { status: 404, body: upstreamBody };
      },
    },
  });

  const response = await withServer(router, '/999/overview');
  assert.equal(response.status, 404);
  assert.deepEqual(await response.json(), upstreamBody);
});

test('createProjectRoutes relays upstream asset filter 400 status and body unchanged', async () => {
  const upstreamBody = { success: false, code: 1, msg: '资产类型不合法' };
  const router = createProjectRoutes({
    projectApi: {
      async listProjectAssets() {
        return { status: 400, body: upstreamBody };
      },
    },
  });

  const response = await withServer(router, '/42/assets?type=unsupported');
  assert.equal(response.status, 400);
  assert.deepEqual(await response.json(), upstreamBody);
});

test('createProjectRoutes rejects browser output_dir before calling export upstream', async () => {
  const calls = {
    getProjectDetail: 0,
    request: 0,
    createProjectExport: 0,
  };
  const router = createProjectRoutes({
    projectApi: {
      async getProjectDetail() {
        calls.getProjectDetail += 1;
        return { status: 200, body: { success: true, code: 0, data: { summary: { id: 8 } } } };
      },
      async request() {
        calls.request += 1;
        return { status: 200, body: { success: true, code: 0, data: { features: [] } } };
      },
      async createProjectExport() {
        calls.createProjectExport += 1;
        return { status: 200, body: { success: true, code: 0, data: { id: 11 } } };
      },
    },
  });

  const response = await withServer(router, '/8/exports', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ format: 'geojson', output_dir: 'D:/tmp' }),
  });
  assert.equal(response.status, 422);
  assert.deepEqual(await response.json(), {
    success: false,
    code: 1,
    msg: '不支持指定服务端输出目录，请移除 output_dir',
  });
  assert.deepEqual(calls, {
    getProjectDetail: 0,
    request: 0,
    createProjectExport: 0,
  });
});

test('createProjectRoutes rejects browser file_path before calling dataset upstream', async () => {
  const calls = { createProjectDataset: 0, request: 0 };
  const router = createProjectRoutes({
    projectApi: {
      async createProjectDataset() {
        calls.createProjectDataset += 1;
        return { status: 200, body: { success: true, code: 0, data: { id: 21 } } };
      },
      async request() {
        calls.request += 1;
        return { status: 200, body: { success: true, code: 0, data: {} } };
      },
    },
  });

  const response = await withServer(router, '/8/datasets', {
    method: 'POST',
    headers: { cookie: 'admin_user_id=7', 'content-type': 'application/json' },
    body: JSON.stringify({ display_name: '影像', dataset_kind: 'imagery', file_path: 'D:/tmp.tif' }),
  });
  assert.equal(response.status, 422);
  assert.deepEqual(await response.json(), {
    success: false,
    code: 1,
    msg: '不支持指定服务端文件路径，请移除 file_path',
  });
  assert.deepEqual(calls, { createProjectDataset: 0, request: 0 });
});

test('createProjectRoutes relays storage_key dataset registration unchanged', async () => {
  const calls = [];
  const router = createProjectRoutes({
    projectApi: {
      async createProjectDataset(projectId, payload, cookie) {
        calls.push({ projectId, payload, cookie });
        return { status: 200, body: { success: true, code: 0, data: { id: 21 } } };
      },
    },
  });

  const payload = { display_name: '影像', dataset_kind: 'imagery', storage_key: 'incoming/2024/a.tif' };
  const response = await withServer(router, '/8/datasets', {
    method: 'POST',
    headers: { cookie: 'admin_user_id=7', 'content-type': 'application/json' },
    body: JSON.stringify(payload),
  });

  assert.equal(response.status, 200);
  assert.deepEqual(calls, [{ projectId: '8', payload, cookie: 'admin_user_id=7' }]);
});

test('createProjectRoutes relays vector exports without BFF domain assembly', async () => {
  const calls = { detail: 0, request: 0, export: 0 };
  let exportPayload = null;
  const router = createProjectRoutes({
    projectApi: {
      async request() {
        calls.request += 1;
        return { status: 200, body: { success: true, code: 0, data: {} } };
      },
      async getProjectDetail() {
        calls.detail += 1;
        return { status: 200, body: { success: true, code: 0, data: {} } };
      },
      async createProjectExport(projectId, payload) {
        calls.export += 1;
        exportPayload = { projectId, payload };
        return { status: 200, body: { success: true, code: 0, data: { id: 11, format: 'geojson' } } };
      },
    },
  });

  const response = await withServer(router, '/8/exports', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ format: 'geojson' }),
  });

  assert.equal(response.status, 200);
  assert.equal((await response.json()).data.id, 11);
  assert.deepEqual(exportPayload, { projectId: 8, payload: { format: 'geojson' } });
  assert.deepEqual(calls, { detail: 0, request: 0, export: 1 });
});

test('createProjectRoutes proxies project inference images through the backend', async () => {
  const calls = [];
  const router = createProjectRoutes({
    projectApi: {
      async getProjectInferenceOutput(projectId, fid, filename, cookie) {
        calls.push({ projectId, fid, filename, cookie });
        return {
          status: 200,
          contentType: 'image/png',
          body: Buffer.from('project-image'),
        };
      },
    },
  });

  const response = await withServer(router, '/7/outputs/inference/101/101+2022.png', {
    headers: { cookie: 'admin_user_id=7' },
  });

  assert.equal(response.status, 200);
  assert.equal(await response.text(), 'project-image');
  assert.match(response.headers.get('content-type') || '', /^image\/png/);
  assert.deepEqual(calls, [{
    projectId: '7',
    fid: '101',
    filename: '101+2022.png',
    cookie: 'admin_user_id=7',
  }]);
});

test('createProjectRoutes relays original imagery list with fid query', async () => {
  const calls = [];
  const router = createProjectRoutes({
    projectApi: {
      // relay() 走通用 request（与 change-matrix 同路径），按方法+路径断言
      async request(method, path, { query, cookie } = {}) {
        calls.push({ method, path, query, cookie });
        return { status: 200, body: { success: true, code: 0, data: { fid: 713, items: [] } } };
      },
    },
  });
  const response = await withServer(router, '/1/mines/original-imagery?fid=713');
  const body = await response.json();
  assert.equal(response.status, 200);
  assert.equal(body.data.fid, 713);
  assert.equal(calls[0].method, 'GET');
  assert.equal(calls[0].path, '/api/projects/1/mines/original-imagery');
  assert.equal(calls[0].query.fid, '713');
});

test('createProjectRoutes relays original imagery download as binary with attachment name', async () => {
  const router = createProjectRoutes({
    projectApi: {
      async getProjectOriginalImageryDownload(projectId, jobId, cookie) {
        assert.equal(projectId, '1');
        assert.match(jobId, /^[0-9a-f-]{36}$/);
        assert.match(String(cookie), /session=/);
        return {
          status: 200,
          contentType: 'image/tiff',
          body: Buffer.from('II* fake-tif-bytes'),
        };
      },
    },
  });
  const response = await withServer(
    router,
    '/1/mines/original-imagery/38d04109-6dda-4a0e-aabb-ccddeeff0011/download',
    { headers: { cookie: 'session=abc' } },
  );
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('content-type'), 'image/tiff');
  assert.equal(await response.text(), 'II* fake-tif-bytes');
});

test('createProjectRoutes rejects a malformed original imagery download job id', async () => {
  const router = createProjectRoutes({
    projectApi: {
      async getProjectOriginalImageryDownload() {
        throw new Error('should not reach upstream');
      },
    },
  });
  const response = await withServer(router, '/1/mines/original-imagery/not-a-uuid/download');
  assert.equal(response.status, 400);
});

test('createProjectRoutes relays project delete with DELETE method', async () => {
  const calls = [];
  const router = createProjectRoutes({
    projectApi: {
      async deleteProject(projectId, cookie) {
        calls.push({ projectId, cookie });
        return { status: 200, body: { success: true, code: 0, data: { id: Number(projectId), deleted: true } } };
      },
    },
  });
  const response = await withServer(router, '/5', { method: 'DELETE' });
  const body = await response.json();
  assert.equal(response.status, 200);
  assert.equal(body.data.deleted, true);
  assert.equal(calls[0].projectId, '5');
});

test('createProjectRoutes relays batch archive with project_ids body', async () => {
  const calls = [];
  const router = createProjectRoutes({
    projectApi: {
      async batchArchiveProjects(body, cookie) {
        calls.push({ body, cookie });
        return { status: 200, body: { success: true, code: 0, data: { succeeded: 1, failed: 0 } } };
      },
    },
  });
  const response = await withServer(router, '/batch/archive', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ project_ids: [3] }),
  });
  const body = await response.json();
  assert.equal(response.status, 200);
  assert.deepEqual(calls[0].body, { project_ids: [3] });
  assert.equal(body.data.succeeded, 1);
});

test('createProjectRoutes relays backup manifest import', async () => {
  const calls = [];
  const router = createProjectRoutes({
    projectApi: {
      async importBackupManifest(projectId, body, cookie) {
        calls.push({ projectId, body });
        return { status: 200, body: { success: true, code: 0, data: { project_id: Number(projectId) } } };
      },
    },
  });
  const manifest = { snapshot_version: 1, summary: {}, mines: [], datasets: [], exports: [], activities: [] };
  const response = await withServer(router, '/7/backups/import', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ manifest }),
  });
  assert.equal(response.status, 200);
  assert.equal(calls[0].projectId, '7');
  assert.deepEqual(calls[0].body.manifest, manifest);
});
