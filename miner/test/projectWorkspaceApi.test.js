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
