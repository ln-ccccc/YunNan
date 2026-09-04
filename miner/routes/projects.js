import { Router } from 'express';
import fs from 'node:fs';
import path from 'node:path';

import { projectApi as defaultProjectApi } from '../services/projectBackend.js';
import { buildProjectExportFeatures } from '../services/projectExportFeatures.js';

function relayJson(res, upstream) {
  res.status(upstream.status || 200).json(upstream.body);
}

function requestCookie(req) {
  return req.headers.cookie || '';
}

export function createProjectRoutes({
  projectApi = defaultProjectApi,
  getMinesData = () => [],
  projectStorageRoot = process.env.PROJECT_STORAGE_ROOT || '/project_storage',
} = {}) {
  const router = Router();
  const storageRoot = path.resolve(projectStorageRoot);

  router.get('/:projectId/outputs/inference/:fid/:filename', (req, res) => {
    const projectId = String(req.params.projectId || '');
    const fid = String(req.params.fid || '');
    const filename = String(req.params.filename || '');
    if (!/^\d+$/.test(projectId) || !/^\d+$/.test(fid)) {
      return res.status(400).json({ error: 'Invalid project output path' });
    }
    const escapedFid = fid.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const allowedName = new RegExp(`^${escapedFid}\\+\\d{4}(?:_src)?\\.png$`);
    if (path.basename(filename) !== filename || !allowedName.test(filename)) {
      return res.status(400).json({ error: 'Invalid project output filename' });
    }
    const outputRoot = path.resolve(
      storageRoot,
      'projects',
      projectId,
      'outputs',
      'inference',
      fid,
    );
    const target = path.resolve(outputRoot, filename);
    if (!target.startsWith(`${outputRoot}${path.sep}`) || !fs.existsSync(target)) {
      return res.status(404).json({ error: 'Project inference output not found' });
    }
    return res.sendFile(target);
  });

  router.get('/', async (req, res) => {
    try {
      const upstream = await projectApi.listProjects({
        name: req.query.name ?? null,
        region: req.query.region ?? null,
        status: req.query.status ?? null,
        monitor_year: req.query.monitor_year ?? null,
      }, requestCookie(req));
      relayJson(res, upstream);
    } catch (error) {
      res.status(502).json({ success: false, code: 1, msg: error?.message || String(error) });
    }
  });

  router.post('/', async (req, res) => {
    try {
      relayJson(res, await projectApi.createProject(req.body || {}, requestCookie(req)));
    } catch (error) {
      res.status(502).json({ success: false, code: 1, msg: error?.message || String(error) });
    }
  });

  const relay = (method, suffix, { body = false, query = false } = {}) => async (req, res) => {
    try {
      const path = `/api/projects/${encodeURIComponent(req.params.projectId)}${suffix(req)}`;
      relayJson(res, await projectApi.request(method, path, {
        ...(body ? { body: req.body || {} } : {}),
        ...(query ? { query: req.query || {} } : {}),
        cookie: requestCookie(req),
      }));
    } catch (error) {
      res.status(502).json({ success: false, code: 1, msg: error?.message || String(error) });
    }
  };

  router.get('/:projectId/spatial', relay('GET', () => '/spatial'));
  router.post('/:projectId/spatial/mines/preview', relay('POST', () => '/spatial/mines/preview', { body: true }));
  router.post('/:projectId/spatial/mines', relay('POST', () => '/spatial/mines', { body: true }));
  router.get('/:projectId/spatial/basemap-candidates', relay('GET', () => '/spatial/basemap-candidates'));
  router.post('/:projectId/spatial/basemaps', relay('POST', () => '/spatial/basemaps', { body: true }));
  router.get('/:projectId/spatial/jobs/:jobId', relay('GET', (req) => `/spatial/jobs/${encodeURIComponent(req.params.jobId)}`));
  router.post('/:projectId/spatial/jobs/:jobId/retry', relay('POST', (req) => `/spatial/jobs/${encodeURIComponent(req.params.jobId)}/retry`, { body: true }));
  router.post('/:projectId/spatial/jobs/:jobId/cancel', relay('POST', (req) => `/spatial/jobs/${encodeURIComponent(req.params.jobId)}/cancel`, { body: true }));
  router.get('/:projectId/map/manifest', relay('GET', () => '/map/manifest'));
  router.get('/:projectId/geojson', relay('GET', () => '/geojson'));
  router.get('/:projectId/stats', relay('GET', () => '/stats'));
  router.get('/:projectId/mines/search', relay('GET', () => '/mines/search', { query: true }));
  router.get('/:projectId/mines/indices', relay('GET', () => '/mines/indices', { query: true }));
  router.get('/:projectId/mines/change-matrix', relay('GET', () => '/mines/change-matrix', { query: true }));
  router.get('/:projectId/mines/trend-report', relay('GET', () => '/mines/trend-report', { query: true }));

  router.get('/:projectId', async (req, res) => {
    try {
      relayJson(res, await projectApi.getProjectDetail(req.params.projectId, requestCookie(req)));
    } catch (error) {
      res.status(502).json({ success: false, code: 1, msg: error?.message || String(error) });
    }
  });

  router.patch('/:projectId', async (req, res) => {
    try {
      relayJson(res, await projectApi.updateProject(req.params.projectId, req.body || {}, requestCookie(req)));
    } catch (error) {
      res.status(502).json({ success: false, code: 1, msg: error?.message || String(error) });
    }
  });

  router.put('/:projectId/mines', async (req, res) => {
    try {
      relayJson(res, await projectApi.replaceProjectMines(req.params.projectId, req.body || {}, requestCookie(req)));
    } catch (error) {
      res.status(502).json({ success: false, code: 1, msg: error?.message || String(error) });
    }
  });

  router.post('/:projectId/datasets', async (req, res) => {
    try {
      relayJson(res, await projectApi.createProjectDataset(req.params.projectId, req.body || {}, requestCookie(req)));
    } catch (error) {
      res.status(502).json({ success: false, code: 1, msg: error?.message || String(error) });
    }
  });

  router.get('/:projectId/timeline', async (req, res) => {
    try {
      relayJson(res, await projectApi.getProjectTimeline(req.params.projectId, requestCookie(req)));
    } catch (error) {
      res.status(502).json({ success: false, code: 1, msg: error?.message || String(error) });
    }
  });

  router.post('/:projectId/archive', async (req, res) => {
    try {
      relayJson(res, await projectApi.archiveProject(req.params.projectId, req.body || {}, requestCookie(req)));
    } catch (error) {
      res.status(502).json({ success: false, code: 1, msg: error?.message || String(error) });
    }
  });

  router.post('/:projectId/restore', async (req, res) => {
    try {
      relayJson(res, await projectApi.restoreProject(req.params.projectId, req.body || {}, requestCookie(req)));
    } catch (error) {
      res.status(502).json({ success: false, code: 1, msg: error?.message || String(error) });
    }
  });

  router.post('/:projectId/exports', async (req, res) => {
    try {
      const projectId = Number(req.params.projectId);
      const payload = { ...(req.body || {}) };
      const format = String(payload.format || '').toLowerCase();
      if (!Array.isArray(payload.features) && (format === 'geojson' || format === 'shp')) {
        const [detailUpstream, geojsonUpstream] = await Promise.all([
          projectApi.getProjectDetail(projectId, requestCookie(req)),
          projectApi.request('GET', `/api/projects/${projectId}/geojson`, { cookie: requestCookie(req) }),
        ]);
        const detailBody = detailUpstream?.body || {};
        const geojsonBody = geojsonUpstream?.body || {};
        if (!detailBody?.data || !geojsonBody?.data) {
          return relayJson(res, detailUpstream);
        }
        payload.features = buildProjectExportFeatures({
          projectDetail: detailBody.data,
          minesData: geojsonBody.data.features || [],
        });
      }
      relayJson(res, await projectApi.createProjectExport(projectId, payload, requestCookie(req)));
    } catch (error) {
      res.status(502).json({ success: false, code: 1, msg: error?.message || String(error) });
    }
  });

  router.get('/:projectId/exports', async (req, res) => {
    try {
      relayJson(res, await projectApi.listProjectExports(req.params.projectId, requestCookie(req)));
    } catch (error) {
      res.status(502).json({ success: false, code: 1, msg: error?.message || String(error) });
    }
  });

  router.post('/:projectId/backups', async (req, res) => {
    try {
      relayJson(res, await projectApi.createProjectBackup(req.params.projectId, req.body || {}, requestCookie(req)));
    } catch (error) {
      res.status(502).json({ success: false, code: 1, msg: error?.message || String(error) });
    }
  });

  router.get('/:projectId/backups', async (req, res) => {
    try {
      relayJson(res, await projectApi.listProjectBackups(req.params.projectId, requestCookie(req)));
    } catch (error) {
      res.status(502).json({ success: false, code: 1, msg: error?.message || String(error) });
    }
  });

  router.post('/:projectId/backups/:backupId/restore', async (req, res) => {
    try {
      relayJson(res, await projectApi.restoreProjectBackup(req.params.projectId, req.params.backupId, req.body || {}, requestCookie(req)));
    } catch (error) {
      res.status(502).json({ success: false, code: 1, msg: error?.message || String(error) });
    }
  });

  return router;
}
