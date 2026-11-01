import http from 'node:http';

// 底图上传传输层（2026-10-31 方案 A / 2026-11-01 后续项①）：BFF 是**纯传输层**
// ——把浏览器的请求体流式转发到后端权威端点（校验/落盘/会话全在后端），
// project_storage 对 BFF 保持只读。JSON 与 octet-stream 一律 pipe 透传
// （express.json 只解析 application/json，其余 content-type 的流未被消费）。

export function resolveForwardTarget(queryFilename, projectId, backendOrigin) {
  const filename = encodeURIComponent(String(queryFilename || ''));
  return `${backendOrigin}/api/projects/${projectId}/spatial/basemap-upload?filename=${filename}`;
}

/** 通用流式转发：req pipe → 后端；后端 JSON 响应透传（状态码/信封原样）。
 * 除 content-type/cookie/content-length 外还必须透传 X-Chunk-Sha256——它是
 * 分块内容校验的唯一凭证，丢了后端 declared_sha256=None、校验静默失效
 * （2026-11-01 审查项）。 */
export function forwardRawStream(req, res, { backendOrigin, path }) {
  const target = `${backendOrigin}${path}`;
  const headers = {
    'content-type': req.headers['content-type'] || 'application/octet-stream',
    cookie: req.headers.cookie || '',
  };
  if (req.headers['content-length']) {
    headers['content-length'] = req.headers['content-length'];
  }
  if (req.headers['x-chunk-sha256']) {
    headers['x-chunk-sha256'] = req.headers['x-chunk-sha256'];
  }
  const upstream = http.request(
    target,
    { method: req.method, headers },
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

export function forwardBasemapUpload(req, res, { backendOrigin, projectId }) {
  forwardRawStream(req, res, {
    backendOrigin,
    path: `/api/projects/${projectId}/spatial/basemap-upload?filename=${encodeURIComponent(String(req.query.filename || ''))}`,
  });
}
