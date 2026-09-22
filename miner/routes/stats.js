import { Router } from 'express';

import { projectApi as defaultProjectApi } from '../services/projectBackend.js';

function relayJson(res, upstream) {
  res.status(upstream.status || 200).json(upstream.body);
}

function requestCookie(req) {
  return req.headers.cookie || '';
}

export function createStatsRoutes({
  projectApi = defaultProjectApi,
} = {}) {
  const router = Router();

  // 跨项目统计概览（M1 主控台看板）：只读聚合，直接透传
  router.get('/overview', async (req, res) => {
    try {
      relayJson(res, await projectApi.request('GET', '/api/stats/overview', {
        cookie: requestCookie(req),
      }));
    } catch (error) {
      console.error('stats route upstream error:', error);
      res.status(502).json({ success: false, code: 1, msg: '上游服务不可用，请稍后重试' });
    }
  });

  return router;
}
