import express from 'express';
import fs from 'fs';
import path from 'path';

// 1×1 透明 PNG：底图锯齿边缘在 bbox 内但磁盘无瓦片的格子以 200+透明回给，
// 消除整屏 404 控制台噪音（业界惯例，MapLibre#1845/OSM 同做法）。
// 仅当该 zoom 目录存在（=资源确实切到此级）时兜底；zoom 目录或 .active
// 缺失仍 404——资源级异常不被透明瓦片掩盖（R3 瓦片巡检语义不变）。
const TRANSPARENT_TILE_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
  'base64'
);

export function createTileRoutes({ projectStorageRoot }) {
  const router = express.Router();
  // projectId/resourceId 为可枚举数字，调用方必须挂 authGuard：
  // 裸路由会被未登录客户端整库拉取底图（2026-09-20 审查 P1）
  router.get('/projects/:projectId/:resourceId/:z/:x/:y.png', (req, res) => {
    const segments = ['projectId', 'resourceId', 'z', 'x', 'y'].map((key) => String(req.params[key] || ''));
    if (!segments.every((value) => /^\d+$/.test(value))) {
      return res.status(400).json({ error: 'Invalid tile path' });
    }
    const [projectId, resourceId, z, x, y] = segments;
    const tileRoot = path.resolve(projectStorageRoot, 'projects', projectId, 'tiles', resourceId);
    if (!fs.existsSync(path.join(tileRoot, '.active'))) {
      return res.status(404).json({ error: 'Project basemap is not active' });
    }
    const zoomDir = path.join(tileRoot, z);
    const tilePath = path.resolve(zoomDir, x, `${y}.png`);
    if (!tilePath.startsWith(`${tileRoot}${path.sep}`)) {
      return res.status(400).json({ error: 'Invalid tile path' });
    }
    if (!fs.existsSync(tilePath)) {
      if (!fs.existsSync(zoomDir)) {
        return res.status(404).json({ error: 'Project tile not found' });
      }
      // 透明兜底只缓存 60s（2026-11-05 生产化检查，迁移审查 P2-1）：此前钉 24h，
      // 底图重切修复后旧浏览器仍显示透明一天；真实瓦片长缓存见下方 sendFile
      res.set('Cache-Control', 'public, max-age=60');
      res.set('Content-Type', 'image/png');
      return res.status(200).end(TRANSPARENT_TILE_PNG);
    }
    res.set('Cache-Control', 'public, max-age=86400');
    return res.sendFile(tilePath);
  });
  return router;
}
