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

async function requestBinary(method, path, { cookie } = {}) {
  const url = new URL(`${backendBaseUrl}${path}`);
  const response = await fetch(url, {
    method,
    headers: cookie ? { cookie } : {},
  });
  return {
    status: response.status,
    contentType: response.headers.get('content-type') || 'application/octet-stream',
    body: Buffer.from(await response.arrayBuffer()),
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
