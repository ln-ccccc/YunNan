import { Router } from 'express';

import { projectApi as defaultProjectApi } from '../services/projectBackend.js';

function relayJson(res, upstream) {
  res.status(upstream.status || 200).json(upstream.body);
}

function requestCookie(req) {
  return req.headers.cookie || '';
}

// 智能解译页（M5.1 只读收编）：KML ROI 解译历史的项目态透传。
// 历史记录属于后端 analysis 域，BFF 只做同构转发不塑形；
// 删除/清空接口暂不开放（避免误删正式推理记录），见 M5 计划。
export function createInterpretationRoutes({
  projectApi = defaultProjectApi,
} = {}) {
  const router = Router();

  router.get('/kml-roi-history', async (req, res) => {
    const projectId = String(req.query.project_id || '');
    if (!/^[1-9]\d*$/.test(projectId)) {
      return res.status(400).json({ success: false, code: 1, msg: '项目参数不合法' });
    }
    const page = String(req.query.page || '1');
    const limit = String(req.query.limit || '20');
    try {
      const query = new URLSearchParams({ page, limit, project_id: projectId });
      const upstream = await projectApi.request(
        'GET',
        `/api/analysis/kml_roi_history?${query.toString()}`,
        { cookie: requestCookie(req) },
      );
      // 展示排序已移前端消费方（M3 塑形收口：BFF 不做展示决策）
      relayJson(res, upstream);
    } catch (error) {
      console.error('interpretation route upstream error:', error);
      res.status(502).json({ success: false, code: 1, msg: '上游服务不可用，请稍后重试' });
    }
  });

  return router;
}
