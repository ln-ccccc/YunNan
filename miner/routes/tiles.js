import express from 'express';

// 2026-11-14 审查问题29：BFF 瓦片路由改为纯传输层——转发 Flask
// /api/projects/<id>/map-resources/<rid>/tiles/<z>/<x>/<y>.png。
// 激活判定（DB status）、锯齿边缺格透明兜底（zoom 目录存在 → 1×1 透明 +
// max-age=60）、真实瓦片长缓存（max-age=86400）等业务与缓存语义全部在后端；
// BFF 不读 project_storage、不判断 .active 标记，存储结构变化不再牵动 Node 侧。
// 鉴权双保险：本路由挂 authGuard（会话经 Flask 校验），转发携带 cookie，
// 后端 @login_required 兜底。
export function createTileRoutes({ backendBaseUrl, fetchImpl = globalThis.fetch }) {
  const router = express.Router();
  // projectId/resourceId 为可枚举数字，调用方必须挂 authGuard：
  // 裸路由会被未登录客户端整库拉取底图（2026-09-20 审查 P1）
  router.get('/projects/:projectId/:resourceId/:z/:x/:y.png', async (req, res) => {
    const segments = ['projectId', 'resourceId', 'z', 'x', 'y'].map((key) => String(req.params[key] || ''));
    if (!segments.every((value) => /^\d+$/.test(value))) {
      return res.status(400).json({ error: 'Invalid tile path' });
    }
    const [projectId, resourceId, z, x, y] = segments;
    try {
      const upstream = await fetchImpl(
        `${backendBaseUrl}/api/projects/${projectId}/map-resources/${resourceId}/tiles/${z}/${x}/${y}.png`,
        { headers: { cookie: req.headers.cookie || '' } }
      );
      const contentType = upstream.headers.get('content-type');
      const cacheControl = upstream.headers.get('cache-control');
      if (contentType) res.set('Content-Type', contentType);
      if (cacheControl) res.set('Cache-Control', cacheControl);
      return res.status(upstream.status).send(Buffer.from(await upstream.arrayBuffer()));
    } catch (error) {
      console.error('[tiles] 后端瓦片转发失败:', error?.message || error);
      if (!res.headersSent) {
        return res.status(502).json({ error: '瓦片服务暂时不可用，请稍后重试' });
      }
    }
  });
  return router;
}
