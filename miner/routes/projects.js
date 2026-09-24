import { Router } from 'express';

import { aggregateLandTypeList } from '../services/dashboardStats.js';
import { projectApi as defaultProjectApi } from '../services/projectBackend.js';

function relayJson(res, upstream) {
  res.status(upstream.status || 200).json(upstream.body);
}

function relayBinary(res, upstream) {
  res.status(upstream.status || 200);
  if (upstream.contentType) {
    res.set('content-type', upstream.contentType);
  }
  res.send(upstream.body);
}

function requestCookie(req) {
  return req.headers.cookie || '';
}

function validatePositiveRouteParam(req, res, next, value) {
  if (!/^[1-9]\d*$/.test(String(value || ''))) {
    return res.status(400).json({
      success: false,
      code: 1,
      msg: '项目参数不合法',
    });
  }
  return next();
}

function validateSpatialJobRouteParam(req, res, next, value) {
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(String(value || ''))) {
    return res.status(400).json({
      success: false,
      code: 1,
      msg: '项目参数不合法',
    });
  }
  return next();
}

export function createProjectRoutes({
  projectApi = defaultProjectApi,
} = {}) {
  const router = Router();

  router.param('projectId', validatePositiveRouteParam);
  router.param('backupId', validatePositiveRouteParam);
  router.param('jobId', validateSpatialJobRouteParam);

  router.get('/:projectId/outputs/inference/:fid/:filename', async (req, res) => {
    const projectId = String(req.params.projectId || '');
    const fid = String(req.params.fid || '');
    const filename = String(req.params.filename || '');
    if (!/^[1-9]\d*$/.test(projectId) || !/^[1-9]\d*$/.test(fid)) {
      return res.status(400).json({ error: 'Invalid project output path' });
    }
    const escapedFid = fid.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const allowedName = new RegExp(`^${escapedFid}\\+\\d{4}(?:_src)?\\.png$`);
    if (filename.includes('/') || filename.includes('\\') || !allowedName.test(filename)) {
      return res.status(400).json({ error: 'Invalid project output filename' });
    }
    try {
      relayBinary(
        res,
        await projectApi.getProjectInferenceOutput(projectId, fid, filename, requestCookie(req)),
      );
    } catch (error) {
      // 上游异常细节只进服务端日志，不回显给浏览器
      console.error('projects route upstream error:', error);
      res.status(502).json({ success: false, code: 1, msg: '上游服务不可用，请稍后重试' });
    }
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
      // 上游异常细节只进服务端日志，不回显给浏览器
      console.error('projects route upstream error:', error);
      res.status(502).json({ success: false, code: 1, msg: '上游服务不可用，请稍后重试' });
    }
  });

  router.post('/', async (req, res) => {
    try {
      relayJson(res, await projectApi.createProject(req.body || {}, requestCookie(req)));
    } catch (error) {
      // 上游异常细节只进服务端日志，不回显给浏览器
      console.error('projects route upstream error:', error);
      res.status(502).json({ success: false, code: 1, msg: '上游服务不可用，请稍后重试' });
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
      // 上游异常细节只进服务端日志，不回显给浏览器
      console.error('projects route upstream error:', error);
      res.status(502).json({ success: false, code: 1, msg: '上游服务不可用，请稍后重试' });
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
  // 修复后地类看板：NXFFX 组合串在 BFF 归类聚合后再下发（参照江西 dashboardStats 塑造层），
  // 其余字段原样透传，后端原始口径不变
  router.get('/:projectId/stats', async (req, res) => {
    try {
      const upstream = await projectApi.request(
        'GET',
        `/api/projects/${encodeURIComponent(req.params.projectId)}/stats`,
        { cookie: requestCookie(req) },
      );
      if (upstream.status === 200 && upstream.body?.data && typeof upstream.body.data === 'object') {
        upstream.body.data.landTypeList = aggregateLandTypeList(upstream.body.data.landTypeList);
      }
      relayJson(res, upstream);
    } catch (error) {
      console.error('projects route upstream error:', error);
      res.status(502).json({ success: false, code: 1, msg: '上游服务不可用，请稍后重试' });
    }
  });
  router.get('/:projectId/mines/search', relay('GET', () => '/mines/search', { query: true }));
  router.get('/:projectId/mines/indices', relay('GET', () => '/mines/indices', { query: true }));
  router.get('/:projectId/mines/change-matrix', relay('GET', () => '/mines/change-matrix', { query: true }));

  // 地物分类原始影像溯源（列表 + 下载）：路径由后端按任务记录解析，BFF 只做转发
  router.get('/:projectId/mines/original-imagery', relay('GET', () => '/mines/original-imagery', { query: true }));

  router.get('/:projectId/imagery/candidates', async (req, res) => {
    try {
      relayJson(res, await projectApi.listImageryCandidates(req.params.projectId, requestCookie(req)));
    } catch (error) {
      console.error('projects route upstream error:', error);
      res.status(502).json({ success: false, code: 1, msg: '上游服务不可用，请稍后重试' });
    }
  });
  router.post('/:projectId/imagery/clip', async (req, res) => {
    try {
      relayJson(res, await projectApi.clipImagery(req.params.projectId, req.body || {}, requestCookie(req)));
    } catch (error) {
      console.error('projects route upstream error:', error);
      res.status(502).json({ success: false, code: 1, msg: '上游服务不可用，请稍后重试' });
    }
  });
  router.post('/:projectId/imagery/slice', async (req, res) => {
    try {
      relayJson(res, await projectApi.sliceImagery(req.params.projectId, req.body || {}, requestCookie(req)));
    } catch (error) {
      console.error('projects route upstream error:', error);
      res.status(502).json({ success: false, code: 1, msg: '上游服务不可用，请稍后重试' });
    }
  });
  router.get('/:projectId/classification-results', relay('GET', () => '/classification-results'));
  router.get('/:projectId/mines/parcel-summary', relay('GET', () => '/mines/parcel-summary', { query: true }));
  router.get('/:projectId/mines/:fid/traceability', relay('GET', (req) => `/mines/${encodeURIComponent(req.params.fid)}/traceability`));

  router.get('/:projectId/mines/original-imagery/:jobId/download', async (req, res) => {
    try {
      const upstream = await projectApi.getProjectOriginalImageryDownload(
        req.params.projectId,
        req.params.jobId,
        requestCookie(req),
      );
      // GB 级流式转发：不整读进内存，边收边发；出错体（非 200 JSON）走一次性透传
      if (upstream.stream && upstream.status === 200) {
        res.status(200);
        res.set('content-type', upstream.contentType);
        if (upstream.contentLength) res.set('content-length', upstream.contentLength);
        const { Readable } = await import('node:stream');
        Readable.fromWeb(upstream.stream).pipe(res);
        return;
      }
      relayBinary(res, upstream);
    } catch (error) {
      console.error('projects route upstream error:', error);
      res.status(502).json({ success: false, code: 1, msg: '上游服务不可用，请稍后重试' });
    }
  });
  router.get('/:projectId/mines/trend-report', relay('GET', () => '/mines/trend-report', { query: true }));

  router.get('/:projectId/overview', async (req, res) => {
    try {
      relayJson(res, await projectApi.getProjectOverview(req.params.projectId, requestCookie(req)));
    } catch (error) {
      // 上游异常细节只进服务端日志，不回显给浏览器
      console.error('projects route upstream error:', error);
      res.status(502).json({ success: false, code: 1, msg: '上游服务不可用，请稍后重试' });
    }
  });

  router.get('/:projectId/assets', async (req, res) => {
    try {
      relayJson(res, await projectApi.listProjectAssets(req.params.projectId, req.query, requestCookie(req)));
    } catch (error) {
      // 上游异常细节只进服务端日志，不回显给浏览器
      console.error('projects route upstream error:', error);
      res.status(502).json({ success: false, code: 1, msg: '上游服务不可用，请稍后重试' });
    }
  });

  router.get('/:projectId', async (req, res) => {
    try {
      relayJson(res, await projectApi.getProjectDetail(req.params.projectId, requestCookie(req)));
    } catch (error) {
      // 上游异常细节只进服务端日志，不回显给浏览器
      console.error('projects route upstream error:', error);
      res.status(502).json({ success: false, code: 1, msg: '上游服务不可用，请稍后重试' });
    }
  });

  router.patch('/:projectId', async (req, res) => {
    try {
      relayJson(res, await projectApi.updateProject(req.params.projectId, req.body || {}, requestCookie(req)));
    } catch (error) {
      // 上游异常细节只进服务端日志，不回显给浏览器
      console.error('projects route upstream error:', error);
      res.status(502).json({ success: false, code: 1, msg: '上游服务不可用，请稍后重试' });
    }
  });

  router.put('/:projectId/mines', async (req, res) => {
    try {
      relayJson(res, await projectApi.replaceProjectMines(req.params.projectId, req.body || {}, requestCookie(req)));
    } catch (error) {
      // 上游异常细节只进服务端日志，不回显给浏览器
      console.error('projects route upstream error:', error);
      res.status(502).json({ success: false, code: 1, msg: '上游服务不可用，请稍后重试' });
    }
  });

  router.post('/:projectId/datasets', async (req, res) => {
    try {
      if (Object.hasOwn(req.body || {}, 'file_path')) {
        return res.status(422).json({
          success: false,
          code: 1,
          msg: '不支持指定服务端文件路径，请移除 file_path',
        });
      }
      relayJson(res, await projectApi.createProjectDataset(req.params.projectId, req.body || {}, requestCookie(req)));
    } catch (error) {
      // 上游异常细节只进服务端日志，不回显给浏览器
      console.error('projects route upstream error:', error);
      res.status(502).json({ success: false, code: 1, msg: '上游服务不可用，请稍后重试' });
    }
  });

  router.get('/:projectId/timeline', async (req, res) => {
    try {
      relayJson(res, await projectApi.getProjectTimeline(req.params.projectId, requestCookie(req)));
    } catch (error) {
      // 上游异常细节只进服务端日志，不回显给浏览器
      console.error('projects route upstream error:', error);
      res.status(502).json({ success: false, code: 1, msg: '上游服务不可用，请稍后重试' });
    }
  });

  // M2 项目管理增强：删除 / 批量 / 快照导入
  router.delete('/:projectId', async (req, res) => {
    try {
      relayJson(res, await projectApi.deleteProject(req.params.projectId, requestCookie(req)));
    } catch (error) {
      console.error('projects route upstream error:', error);
      res.status(502).json({ success: false, code: 1, msg: '上游服务不可用，请稍后重试' });
    }
  });

  router.post('/batch/archive', async (req, res) => {
    try {
      relayJson(res, await projectApi.batchArchiveProjects(req.body || {}, requestCookie(req)));
    } catch (error) {
      console.error('projects route upstream error:', error);
      res.status(502).json({ success: false, code: 1, msg: '上游服务不可用，请稍后重试' });
    }
  });

  router.post('/batch/delete', async (req, res) => {
    try {
      relayJson(res, await projectApi.batchDeleteProjects(req.body || {}, requestCookie(req)));
    } catch (error) {
      console.error('projects route upstream error:', error);
      res.status(502).json({ success: false, code: 1, msg: '上游服务不可用，请稍后重试' });
    }
  });

  router.post('/:projectId/backups/import', async (req, res) => {
    try {
      relayJson(res, await projectApi.importBackupManifest(req.params.projectId, req.body || {}, requestCookie(req)));
    } catch (error) {
      console.error('projects route upstream error:', error);
      res.status(502).json({ success: false, code: 1, msg: '上游服务不可用，请稍后重试' });
    }
  });

  router.post('/:projectId/archive', async (req, res) => {
    try {
      relayJson(res, await projectApi.archiveProject(req.params.projectId, req.body || {}, requestCookie(req)));
    } catch (error) {
      // 上游异常细节只进服务端日志，不回显给浏览器
      console.error('projects route upstream error:', error);
      res.status(502).json({ success: false, code: 1, msg: '上游服务不可用，请稍后重试' });
    }
  });

  router.post('/:projectId/restore', async (req, res) => {
    try {
      relayJson(res, await projectApi.restoreProject(req.params.projectId, req.body || {}, requestCookie(req)));
    } catch (error) {
      // 上游异常细节只进服务端日志，不回显给浏览器
      console.error('projects route upstream error:', error);
      res.status(502).json({ success: false, code: 1, msg: '上游服务不可用，请稍后重试' });
    }
  });

  router.post('/:projectId/exports', async (req, res) => {
    try {
      if (Object.hasOwn(req.body || {}, 'output_dir')) {
        return res.status(422).json({
          success: false,
          code: 1,
          msg: '不支持指定服务端输出目录，请移除 output_dir',
        });
      }
      const projectId = Number(req.params.projectId);
      relayJson(res, await projectApi.createProjectExport(projectId, req.body || {}, requestCookie(req)));
    } catch (error) {
      // 上游异常细节只进服务端日志，不回显给浏览器
      console.error('projects route upstream error:', error);
      res.status(502).json({ success: false, code: 1, msg: '上游服务不可用，请稍后重试' });
    }
  });

  router.get('/:projectId/exports', async (req, res) => {
    try {
      relayJson(res, await projectApi.listProjectExports(req.params.projectId, requestCookie(req)));
    } catch (error) {
      // 上游异常细节只进服务端日志，不回显给浏览器
      console.error('projects route upstream error:', error);
      res.status(502).json({ success: false, code: 1, msg: '上游服务不可用，请稍后重试' });
    }
  });

  router.post('/:projectId/backups', async (req, res) => {
    try {
      relayJson(res, await projectApi.createProjectBackup(req.params.projectId, req.body || {}, requestCookie(req)));
    } catch (error) {
      // 上游异常细节只进服务端日志，不回显给浏览器
      console.error('projects route upstream error:', error);
      res.status(502).json({ success: false, code: 1, msg: '上游服务不可用，请稍后重试' });
    }
  });

  router.get('/:projectId/backups', async (req, res) => {
    try {
      relayJson(res, await projectApi.listProjectBackups(req.params.projectId, requestCookie(req)));
    } catch (error) {
      // 上游异常细节只进服务端日志，不回显给浏览器
      console.error('projects route upstream error:', error);
      res.status(502).json({ success: false, code: 1, msg: '上游服务不可用，请稍后重试' });
    }
  });

  router.post('/:projectId/backups/:backupId/restore', async (req, res) => {
    try {
      relayJson(res, await projectApi.restoreProjectBackup(req.params.projectId, req.params.backupId, req.body || {}, requestCookie(req)));
    } catch (error) {
      // 上游异常细节只进服务端日志，不回显给浏览器
      console.error('projects route upstream error:', error);
      res.status(502).json({ success: false, code: 1, msg: '上游服务不可用，请稍后重试' });
    }
  });

  return router;
}
