import express from 'express';
import cors from 'cors';
import fs from 'fs';
import path from 'path';
import dotenv from 'dotenv';
import geoviewRoutes from './routes/geoview.js';
import { createProjectRoutes } from './routes/projects.js';
import { authBackend } from './services/authBackend.js';
import { relayBackendResponse, requireMinerAuth } from './services/authProxy.js';
import { inferenceBackend } from './services/inferenceBackend.js';
import { saveKmlUpload } from './services/kmlUpload.js';

dotenv.config();

const app = express();
const port = process.env.PORT ? Number(process.env.PORT) : 8000;
const startupCwd = process.cwd();
console.log(`[Startup] miner cwd=${startupCwd}`);
if (/wsl|\\\\wsl\\.localhost/i.test(startupCwd)) {
  console.warn('[Startup] Warning: running from WSL path; expected E:\\GeoView\\miner');
}

// Enable CORS and JSON parsing
app.use(cors());
app.use(express.json({ limit: '60mb' }));
const authGuard = requireMinerAuth({ sessionApi: authBackend.session });
app.use('/api/geoview', authGuard, geoviewRoutes);

app.post('/api/auth/login', async (req, res) => {
  relayBackendResponse(res, await authBackend.login(req.body || {}));
});

app.get('/api/auth/session', async (req, res) => {
  relayBackendResponse(res, await authBackend.session(req.headers.cookie || ''));
});

app.post('/api/auth/logout', async (req, res) => {
  relayBackendResponse(res, await authBackend.logout(req.headers.cookie || ''));
});

app.use('/api/projects', authGuard, createProjectRoutes());

app.get('/tiles/:z/:x/:y.png', (req, res) => {
  return res.status(410).json({ error: '全局瓦片接口已停用，请使用项目瓦片地址' });
});

const projectStorageRoot = path.resolve(process.env.PROJECT_STORAGE_ROOT || '/project_storage');
app.get('/tiles/projects/:projectId/:resourceId/:z/:x/:y.png', (req, res) => {
  const segments = ['projectId', 'resourceId', 'z', 'x', 'y'].map((key) => String(req.params[key] || ''));
  if (!segments.every((value) => /^\d+$/.test(value))) {
    return res.status(400).json({ error: 'Invalid tile path' });
  }
  const [projectId, resourceId, z, x, y] = segments;
  const tileRoot = path.resolve(projectStorageRoot, 'projects', projectId, 'tiles', resourceId);
  if (!fs.existsSync(path.join(tileRoot, '.active'))) {
    return res.status(404).json({ error: 'Project basemap is not active' });
  }
  const tilePath = path.resolve(tileRoot, z, x, `${y}.png`);
  if (!tilePath.startsWith(`${tileRoot}${path.sep}`) || !fs.existsSync(tilePath)) {
    return res.status(404).json({ error: 'Project tile not found' });
  }
  return res.sendFile(tilePath);
});

const changeMatrixStaticDir = path.resolve(process.cwd(), 'change_matrix_outputs');
app.use('/change-matrix-outputs', authGuard, express.static(changeMatrixStaticDir));

const defaultKmlUploadRoot = path.resolve(process.cwd(), 'uploads', 'kml');

// Global Dali files are intentionally not loaded. Project map endpoints read
// only the active resources registered for the requested project.

// --- API Endpoints ---

const retiredGlobalMapEndpoints = [
  '/api/stats',
  '/api/geojson',
  '/api/mines/search',
  '/api/mines/indices',
  '/api/mines/change-matrix',
  '/api/mines/change-area-summary',
  '/api/mines/trend-report',
  '/api/mines/ndvi',
];
app.all(retiredGlobalMapEndpoints, authGuard, (_req, res) => {
  res.status(410).json({ error: '全局地图接口已停用，请使用带项目 ID 的接口' });
});

app.use('/api/kml', authGuard);
app.post('/api/kml/upload', (req, res) => {
  try {
    const result = saveKmlUpload({
      uploadRoot: defaultKmlUploadRoot,
      filename: req.body?.filename,
      content: req.body?.content
    });
    res.json(result);
  } catch (err) {
    res.status(400).json({
      error: err?.message || 'KML 上传失败',
      next: '请确认文件扩展名为 .kml，且文件内容非空'
    });
  }
});


app.use('/api/inference', authGuard);
const relayInferenceJobCreate = async (req, res) => {
  try {
    return relayBackendResponse(
      res,
      await inferenceBackend.createJob(req.body || {}, req.headers.cookie || ''),
    );
  } catch (err) {
    return res.status(502).json({ success: false, code: 1, msg: err?.message || String(err) });
  }
};
app.post('/api/inference/jobs', relayInferenceJobCreate);
app.post('/api/inference/kml-roi', relayInferenceJobCreate);

app.get('/api/inference/jobs/:jobId', async (req, res) => {
  relayBackendResponse(res, await inferenceBackend.getJob(req.params.jobId, req.headers.cookie || ''));
});

app.post('/api/inference/jobs/:jobId/cancel', async (req, res) => {
  relayBackendResponse(res, await inferenceBackend.cancelJob(req.params.jobId, req.headers.cookie || ''));
});

app.get('/api/inference/capabilities', async (req, res) => {
  relayBackendResponse(res, await inferenceBackend.getCapabilities(req.headers.cookie || ''));
});
app.listen(port, () => {
  console.log(`Server running at http://localhost:${port}`);
  console.log('Mode: Local File System (No Database)');
});
