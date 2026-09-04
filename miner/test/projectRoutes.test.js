import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';

import express from 'express';

import { createProjectRoutes } from '../routes/projects.js';

async function withServer(handler) {
  const app = express();
  app.use(express.json({ limit: '20mb' }));
  app.use('/api/projects', handler);
  const server = await new Promise((resolve) => {
    const instance = app.listen(0, () => resolve(instance));
  });
  const { port } = server.address();
  try {
    return await fetch(`http://127.0.0.1:${port}`);
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
    getMinesData: () => [],
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

test('createProjectRoutes enriches geojson exports with live mine geometries', async () => {
  let exportPayload = null;
  const router = createProjectRoutes({
    projectApi: {
      async request() {
        return {
          status: 200,
          body: {
            success: true,
            code: 0,
            data: {
              type: 'FeatureCollection',
              features: [
                {
                  type: 'Feature',
                  geometry: {
                    type: 'Polygon',
                    coordinates: [[[102, 24], [102.1, 24], [102.1, 24.1], [102, 24.1], [102, 24]]],
                  },
                  properties: { FID_1: 201, mine_name: '矿山C' },
                },
              ],
            },
          },
        };
      },
      async getProjectDetail() {
        return {
          status: 200,
          body: {
            success: true,
            code: 0,
            data: {
              summary: { id: 8 },
              mines: [{ mine_fid: 201, mine_name_snapshot: '矿山C' }],
              datasets: [{ id: 3001, mine_fid: 201, dataset_kind: 'report', year_start: 2024, year_end: 2025 }],
            },
          },
        };
      },
      async createProjectExport(projectId, payload) {
        exportPayload = { projectId, payload };
        return {
          status: 200,
          body: { success: true, code: 0, data: { id: 11, format: 'geojson' } },
        };
      },
    },
    getMinesData: () => [
      {
        type: 'Feature',
        geometry: {
          type: 'Polygon',
          coordinates: [[[102, 24], [102.1, 24], [102.1, 24.1], [102, 24.1], [102, 24]]],
        },
        properties: { FID_1: 201, mine_name: '矿山C' },
      },
    ],
  });

  const app = express();
  app.use(express.json({ limit: '20mb' }));
  app.use('/api/projects', router);
  const server = await new Promise((resolve) => {
    const instance = app.listen(0, () => resolve(instance));
  });
  const { port } = server.address();
  try {
    const response = await fetch(`http://127.0.0.1:${port}/api/projects/8/exports`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ format: 'geojson', output_dir: 'D:/tmp' }),
    });
    const body = await response.json();
    assert.equal(response.status, 200);
    assert.equal(body.data.id, 11);
    assert.equal(exportPayload.projectId, 8);
    assert.equal(exportPayload.payload.features.length, 1);
    assert.equal(exportPayload.payload.features[0].properties.project_id, 8);
    assert.equal(exportPayload.payload.features[0].properties.dataset_id, 3001);
  } finally {
    await new Promise((resolve, reject) => server.close((err) => (err ? reject(err) : resolve())));
  }
});

test('createProjectRoutes serves project inference images from scoped storage', async () => {
  const storageRoot = await mkdtemp(path.join(tmpdir(), 'project-output-'));
  const outputDir = path.join(storageRoot, 'projects', '7', 'outputs', 'inference', '101');
  await mkdir(outputDir, { recursive: true });
  await writeFile(path.join(outputDir, '101+2022.png'), Buffer.from('project-image'));
  const router = createProjectRoutes({
    projectStorageRoot: storageRoot,
    projectApi: {},
  });
  const app = express();
  app.use('/api/projects', router);
  const server = await new Promise((resolve) => {
    const instance = app.listen(0, () => resolve(instance));
  });
  const { port } = server.address();
  try {
    const response = await fetch(
      `http://127.0.0.1:${port}/api/projects/7/outputs/inference/101/101+2022.png`,
    );
    assert.equal(response.status, 200);
    assert.equal(await response.text(), 'project-image');
    assert.match(response.headers.get('content-type') || '', /^image\/png/);
  } finally {
    await new Promise((resolve, reject) => server.close((err) => (err ? reject(err) : resolve())));
    await rm(storageRoot, { recursive: true, force: true });
  }
});
