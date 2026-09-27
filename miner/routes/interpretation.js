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
      // 展示排序：年份降序、同年份按矿山 ID 升序、未知年份沉底
      //（后端按推理先后自然序返回，直出会显得杂乱——2026-09-25 UI 巡检实锤）
      if (upstream.status === 200 && Array.isArray(upstream.body?.data)) {
        upstream.body.data.sort((a, b) => {
          const ya = Number(a?.data?.year) || 0;
          const yb = Number(b?.data?.year) || 0;
          if (yb !== ya) return yb - ya;
          const fa = Number(a?.data?.fid) || 0;
          const fb = Number(b?.data?.fid) || 0;
          if (fb !== fa) return fa - fb;
          return String(a?.data?.file || '').localeCompare(String(b?.data?.file || ''), 'zh-CN');
        });
      }
      relayJson(res, upstream);
    } catch (error) {
      console.error('interpretation route upstream error:', error);
      res.status(502).json({ success: false, code: 1, msg: '上游服务不可用，请稍后重试' });
    }
  });

  return router;
}
