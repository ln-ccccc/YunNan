const backendBaseUrl = (process.env.GEOVIEW_BACKEND_URL || 'http://localhost:5008').replace(/\/$/, '');

async function requestJson(method, path, { query, body, cookie } = {}) {
  const url = new URL(`${backendBaseUrl}${path}`);
  Object.entries(query || {}).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== '') {
      url.searchParams.set(key, String(value));
    }
  });

  const response = await fetch(url, {
    method,
    headers: {
      ...(body ? { 'content-type': 'application/json' } : {}),
      ...(cookie ? { cookie } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(15000),
  });
  const text = await response.text();
  let parsed;
  try {
    parsed = text ? JSON.parse(text) : {};
  } catch (_) {
    parsed = { success: false, code: 1, msg: text || 'Invalid JSON response' };
  }
  return { status: response.status, body: parsed };
}

function positiveRouteId(value, fieldName) {
  const raw = String(value ?? '');
  if (!/^[1-9]\d*$/.test(raw)) {
    throw new Error(`${fieldName}参数不合法`);
  }
  return raw;
}

async function requestBinary(method, path, { cookie, timeoutMs = 30000 } = {}) {
  const url = new URL(`${backendBaseUrl}${path}`);
  const response = await fetch(url, {
    method,
    headers: cookie ? { cookie } : {},
    signal: AbortSignal.timeout(timeoutMs),
  });
  return {
    status: response.status,
    contentType: response.headers.get('content-type') || 'application/octet-stream',
    body: Buffer.from(await response.arrayBuffer()),
  };
}

/**
 * GB 级大文件流式转发：不整读进内存（requestBinary 的 arrayBuffer 会让 5GB
 * 影像在 BFF 驻留 5GB 堆，两路并发即 OOM）。返回 web 流由路由层 pipe 给响应；
 * 超时只约束建立阶段（30s），body 读取无总时限——后端 gunicorn 自带 1800s 兜底。
 */
async function requestBinaryStream(method, path, { cookie, connectTimeoutMs = 30000 } = {}) {
  const url = new URL(`${backendBaseUrl}${path}`);
  const response = await fetch(url, {
    method,
    headers: cookie ? { cookie } : {},
    signal: AbortSignal.timeout(connectTimeoutMs),
  });
  return {
    status: response.status,
    contentType: response.headers.get('content-type') || 'application/octet-stream',
    contentLength: response.headers.get('content-length'),
    stream: response.body,
  };
}

export const projectApi = {
  request(method, path, options = {}) {
    return requestJson(method, path, options);
  },
  listProjects(query, cookie) {
    return requestJson('GET', '/api/projects', { query, cookie });
  },
  createProject(payload, cookie) {
    return requestJson('POST', '/api/projects', { body: payload, cookie });
  },
  getProjectInferenceOutput(projectId, fid, filename, cookie) {
    const safeProjectId = positiveRouteId(projectId, '项目');
    const safeFid = positiveRouteId(fid, '矿山');
    const rawFilename = String(filename ?? '');
    const allowedFilename = new RegExp(`^${safeFid}\\+\\d{4}(?:_src)?\\.png$`);
    if (!allowedFilename.test(rawFilename)) {
      throw new Error('推理结果文件名不合法');
    }
    return requestBinary(
      'GET',
      `/api/projects/${safeProjectId}/outputs/inference/${safeFid}/${encodeURIComponent(rawFilename)}`,
      { cookie },
    );
  },
  getProjectDetail(projectId, cookie) {
    return requestJson('GET', `/api/projects/${projectId}`, { cookie });
  },
  getProjectOriginalImageryDownload(projectId, jobId, cookie) {
    const safeProjectId = positiveRouteId(projectId, '项目');
    const safeJobId = String(jobId ?? '');
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(safeJobId)) {
      throw new Error('推理任务标识不合法');
    }
    // 原始影像可达 GB 级：流式转发，BFF 内存占用与文件大小解耦（2026-09-22 审查 P2）
    return requestBinaryStream(
      'GET',
      `/api/projects/${safeProjectId}/mines/original-imagery/${safeJobId}/download`,
      { cookie },
    );
  },
  getProjectOverview(projectId, cookie) {
    return requestJson('GET', `/api/projects/${projectId}/overview`, { cookie });
  },
  listProjectAssets(projectId, query, cookie) {
    return requestJson('GET', `/api/projects/${projectId}/assets`, { query, cookie });
  },
  updateProject(projectId, payload, cookie) {
    return requestJson('PATCH', `/api/projects/${projectId}`, { body: payload, cookie });
  },
  replaceProjectMines(projectId, payload, cookie) {
    return requestJson('PUT', `/api/projects/${projectId}/mines`, { body: payload, cookie });
  },
  createProjectDataset(projectId, payload, cookie) {
    return requestJson('POST', `/api/projects/${projectId}/datasets`, { body: payload, cookie });
  },
  getProjectTimeline(projectId, cookie) {
    return requestJson('GET', `/api/projects/${projectId}/timeline`, { cookie });
  },
  archiveProject(projectId, payload = {}, cookie) {
    return requestJson('POST', `/api/projects/${projectId}/archive`, { body: payload, cookie });
  },
  restoreProject(projectId, payload = {}, cookie) {
    return requestJson('POST', `/api/projects/${projectId}/restore`, { body: payload, cookie });
  },
  createProjectExport(projectId, payload, cookie) {
    return requestJson('POST', `/api/projects/${projectId}/exports`, { body: payload, cookie });
  },
  listProjectExports(projectId, cookie) {
    return requestJson('GET', `/api/projects/${projectId}/exports`, { cookie });
  },
  createProjectBackup(projectId, payload, cookie) {
    return requestJson('POST', `/api/projects/${projectId}/backups`, { body: payload, cookie });
  },
  listProjectBackups(projectId, cookie) {
    return requestJson('GET', `/api/projects/${projectId}/backups`, { cookie });
  },
  restoreProjectBackup(projectId, backupId, payload = {}, cookie) {
    return requestJson('POST', `/api/projects/${projectId}/backups/${backupId}/restore`, { body: payload, cookie });
  },
};
