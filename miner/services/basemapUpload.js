import http from 'node:http';

// 底图网页上传（2026-10-31 方案 A 重构）：BFF 是**纯传输层**——把浏览器的
// octet-stream 流式转发到后端权威端点（后端校验扩展名/文件名/重名/2GB 并落盘
// incoming/，见 spatial_service.receive_basemap_upload）。BFF 不落盘业务数据、
// 不复制校验逻辑（单一真源在后端），project_storage 挂载保持只读。

export function resolveForwardTarget(queryFilename, projectId, backendOrigin) {
  const filename = encodeURIComponent(String(queryFilename || ''));
  return `${backendOrigin}/api/projects/${projectId}/spatial/basemap-upload?filename=${filename}`;
}

/**
 * 流式转发：req pipe → 后端权威端点；后端 JSON 响应原样透传（含 409/400/413）。
 * 鉴权已由 router 挂载的 authGuard 完成（矿工 session），cookie 随流转交给后端
 * （后端 @login_required 校验自己的 session）。
 */
export function forwardBasemapUpload(req, res, { backendOrigin, projectId }) {
  const target = resolveForwardTarget(req.query.filename, projectId, backendOrigin);
  const upstream = http.request(
    target,
    {
      method: 'POST',
      headers: {
        'content-type': 'application/octet-stream',
        ...(req.headers['content-length']
          ? { 'content-length': req.headers['content-length'] }
          : {}),
        cookie: req.headers.cookie || '',
      },
    },
    (upstreamRes) => {
      let body = '';
      upstreamRes.on('data', (chunk) => { body += chunk; });
      upstreamRes.on('end', () => {
        res.status(upstreamRes.statusCode || 502);
        res.set('content-type', upstreamRes.headers['content-type'] || 'application/json');
        res.send(body);
      });
    },
  );
  upstream.on('error', (err) => {
    console.error('[basemap-upload] 后端转发失败:', err.message);
    if (!res.headersSent) {
      res.status(502).json({ success: false, code: 1, msg: '底图上传服务暂时不可用' });
    }
  });
  req.on('error', () => upstream.destroy());
  req.pipe(upstream);
}
