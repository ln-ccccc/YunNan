import express from 'express';
import cors from 'cors';
import fs from 'fs';
import path from 'path';
import dotenv from 'dotenv';
import { createProjectRoutes } from './routes/projects.js';
import { createStatsRoutes } from './routes/stats.js';
import { createInterpretationRoutes } from './routes/interpretation.js';
import { createTileRoutes } from './routes/tiles.js';
import { authBackend } from './services/authBackend.js';
import { relayBackendResponse, requireMinerAuth } from './services/authProxy.js';
import { inferenceBackend } from './services/inferenceBackend.js';

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
// 大体积 JSON 仅限确需大请求体的路由（矿山 GeoJSON 导入，前端另有 50MB 上限）；
// 全局默认收到 1MB，避免未登录面（如 /api/auth/login）被打大体（2026-09-20 审查 P2）。
// /api/kml/upload 死端点已删（2026-11-14 审查问题29）：全仓无前端调用方，
// KML 内容由解译流程直接提交 Flask，BFF 不落盘业务数据。
app.use('/api/projects', express.json({ limit: '60mb' }));
app.use(express.json({ limit: '1mb' }));
const authGuard = requireMinerAuth({ sessionApi: authBackend.session });
// /api/geoview 下的光谱实时数据代理已删除：Flask 后端从未实现该路由，
// 每次请求必然 404 且被静默吞掉（2026-09-15 审计 Y2-3），
// 守护断言见 test/deadLinkGuards.test.js。

// 认证后端不可达/超时等异常统一 502 JSON；格式与 authProxy.requireMinerAuth 的失败响应对齐，
// 服务端 console.error 留痕，但不把异常细节回显给客户端。
const AUTH_GATEWAY_ERROR = { success: false, code: 1, msg: '认证服务暂时不可用，请稍后重试' };

app.post('/api/auth/login', async (req, res) => {
  try {
    relayBackendResponse(res, await authBackend.login(req.body || {}));
  } catch (error) {
    console.error('[auth] login 网关请求失败:', error?.message || error);
    res.status(502).json(AUTH_GATEWAY_ERROR);
  }
});

app.get('/api/auth/session', async (req, res) => {
  try {
    relayBackendResponse(res, await authBackend.session(req.headers.cookie || ''));
  } catch (error) {
    console.error('[auth] session 网关请求失败:', error?.message || error);
    res.status(502).json(AUTH_GATEWAY_ERROR);
  }
});

app.post('/api/auth/change-password', async (req, res) => {
  try {
    relayBackendResponse(res, await authBackend.changePassword(req.body || {}, req.headers.cookie || ''));
  } catch (error) {
    console.error('[auth] change-password 网关请求失败:', error?.message || error);
    res.status(502).json(AUTH_GATEWAY_ERROR);
  }
});

app.post('/api/auth/logout', async (req, res) => {
  try {
    relayBackendResponse(res, await authBackend.logout(req.headers.cookie || ''));
  } catch (error) {
    console.error('[auth] logout 网关请求失败:', error?.message || error);
    res.status(502).json(AUTH_GATEWAY_ERROR);
  }
});

app.use('/api/projects', authGuard, createProjectRoutes());
app.use('/api/stats', authGuard, createStatsRoutes());
app.use('/api/interpretation', authGuard, createInterpretationRoutes());

app.get('/tiles/:z/:x/:y.png', (req, res) => {
  return res.status(410).json({ error: '全局瓦片接口已停用，请使用项目瓦片地址' });
});

// 项目瓦片与 change-matrix 同为项目数据出口，必须同过 authGuard：
// projectId/resourceId 为可枚举数字，裸路由会被未登录客户端整库拉取底图（2026-09-20 审查 P1）。
// 瓦片语义（激活/透明兜底/缓存头）全在后端，BFF 纯转发（2026-11-14 审查问题29）
const backendBaseUrl = (process.env.GEOVIEW_BACKEND_URL || 'http://localhost:5008').replace(/\/$/, '');
app.use('/tiles', authGuard, createTileRoutes({ backendBaseUrl }));

const changeMatrixStaticDir = path.resolve(process.cwd(), 'change_matrix_outputs');
app.use('/change-matrix-outputs', authGuard, express.static(changeMatrixStaticDir));

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

app.use('/api/inference', authGuard);
const INFERENCE_GATEWAY_ERROR = { success: false, code: 1, msg: '推理服务暂时不可用，请稍后重试' };
const relayInferenceJobCreate = async (req, res) => {
  try {
    return relayBackendResponse(
      res,
      await inferenceBackend.createJob(req.body || {}, req.headers.cookie || ''),
    );
  } catch (err) {
    console.error('[inference] 创建任务网关请求失败:', err?.message || err);
    return res.status(502).json(INFERENCE_GATEWAY_ERROR);
  }
};
app.post('/api/inference/jobs', relayInferenceJobCreate);
app.post('/api/inference/kml-roi', relayInferenceJobCreate);

app.get('/api/inference/jobs/:jobId', async (req, res) => {
  try {
    relayBackendResponse(res, await inferenceBackend.getJob(req.params.jobId, req.headers.cookie || ''));
  } catch (err) {
    console.error('[inference] 查询任务网关请求失败:', err?.message || err);
    res.status(502).json(INFERENCE_GATEWAY_ERROR);
  }
});

app.post('/api/inference/jobs/:jobId/cancel', async (req, res) => {
  try {
    relayBackendResponse(res, await inferenceBackend.cancelJob(req.params.jobId, req.headers.cookie || ''));
  } catch (err) {
    console.error('[inference] 取消任务网关请求失败:', err?.message || err);
    res.status(502).json(INFERENCE_GATEWAY_ERROR);
  }
});

app.get('/api/inference/capabilities', async (req, res) => {
  try {
    relayBackendResponse(res, await inferenceBackend.getCapabilities(req.headers.cookie || ''));
  } catch (err) {
    console.error('[inference] capabilities 网关请求失败:', err?.message || err);
    res.status(502).json(INFERENCE_GATEWAY_ERROR);
  }
});
app.listen(port, () => {
  console.log(`Server running at http://localhost:${port}`);
  console.log('Mode: Local File System (No Database)');
});
