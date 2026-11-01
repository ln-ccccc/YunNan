import assert from 'node:assert/strict';
import test from 'node:test';

import { createProjectWorkspaceApi } from '../src/projectWorkspace/projectWorkspaceApi.js';


test('project workspace api preserves public filters and rejects unsafe path fields', async () => {
  const calls = [];
  const http = {
    get(url, config) {
      calls.push({ method: 'get', url, config });
      return Promise.resolve({ data: { success: true, data: {} } });
    },
    post(url, body) {
      calls.push({ method: 'post', url, body });
      return Promise.resolve({ data: { success: true, data: {} } });
    },
    patch() {
      throw new Error('not used in this test');
    },
    put() {
      throw new Error('not used in this test');
    },
  };
  const api = createProjectWorkspaceApi({ http });

  assert.equal(api.loadDetail, undefined);
  assert.equal(api.replaceProjectMines, undefined);

  await api.loadOverview(42);
  await api.loadAssets(42, { type: 'imagery', status: 'failed' });
  await api.createExport(42, { format: 'geojson' });
  await api.createSnapshot(42);

  assert.deepEqual(calls, [
    { method: 'get', url: '/api/projects/42/overview', config: undefined },
    {
      method: 'get',
      url: '/api/projects/42/assets',
      config: { params: { type: 'imagery', status: 'failed' } },
    },
    { method: 'post', url: '/api/projects/42/exports', body: { format: 'geojson' } },
    { method: 'post', url: '/api/projects/42/backups', body: {} },
  ]);
  assert.throws(
    () => api.createExport(42, { format: 'csv', output_dir: 'D:/tmp' }),
    /不支持 output_dir/,
  );
  assert.throws(
    () => api.registerDataset(42, {
      display_name: '影像',
      dataset_kind: 'imagery',
      file_path: 'D:/tmp.tif',
    }),
    /不支持 file_path/,
  );
  assert.throws(
    () => api.registerDataset(42, {
      display_name: '影像',
      dataset_kind: 'imagery',
      storage_key: 'incoming/../escape.tif',
    }),
    /incoming\//,
  );
});


test('project workspace api turns failed envelopes into errors', async () => {
  const api = createProjectWorkspaceApi({
    http: {
      get() {
        return Promise.resolve({ data: { success: false, msg: '项目不存在' } });
      },
    },
  });

  await assert.rejects(() => api.loadOverview(404), /项目不存在/);
});

test('uploadBasemap streams the file with encoded filename and progress callback', async () => {
  const calls = [];
  const file = { name: '大理 底图 2024.tif', size: 1024 };
  const http = {
    post(url, body, config) {
      calls.push({ url, body, config });
      config.onUploadProgress({ loaded: 512, total: 1024 });
      return Promise.resolve({
        data: { success: true, data: { filename: '大理 底图 2024.tif', candidate: 'incoming/大理 底图 2024.tif' } },
      });
    },
  };
  const api = createProjectWorkspaceApi({ http });
  const progressValues = [];
  const uploaded = await api.uploadBasemap(10, file, (percent) => progressValues.push(percent));

  assert.deepEqual(calls, [{
    url: `/api/projects/10/spatial/basemap-upload?filename=${encodeURIComponent('大理 底图 2024.tif')}`,
    body: file,
    config: {
      headers: { 'Content-Type': 'application/octet-stream' },
      onUploadProgress: calls[0].config.onUploadProgress, // 函数同一性由下方行为断言验证
    },
  }]);
  assert.deepEqual(progressValues, [50], 'loaded/total 必须换算为整数百分比');
  assert.equal(uploaded.candidate, 'incoming/大理 底图 2024.tif');
});

test('uploadBasemap turns failed envelopes into errors', async () => {
  const api = createProjectWorkspaceApi({
    http: {
      post() {
        return Promise.resolve({ data: { success: false, msg: 'incoming/ 已存在同名底图，请重命名后重试' } });
      },
    },
  });

  await assert.rejects(
    () => api.uploadBasemap(10, { name: 'x.tif', size: 1 }),
    /已存在同名底图/,
  );
});
