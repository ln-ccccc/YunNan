const defaultBackendBaseUrl = (process.env.GEOVIEW_BACKEND_URL || 'http://localhost:5008').replace(/\/$/, '');


export function createInferenceBackend({ fetchImpl = fetch, backendBaseUrl = defaultBackendBaseUrl } = {}) {
  const baseUrl = String(backendBaseUrl).replace(/\/$/, '');

  async function request(method, path, { body, cookie = '' } = {}) {
    const response = await fetchImpl(`${baseUrl}${path}`, {
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

  return {
    createJob(payload, cookie = '') {
      return request('POST', '/api/inference/jobs', { body: payload, cookie });
    },
    getJob(jobId, cookie = '') {
      return request('GET', `/api/inference/jobs/${encodeURIComponent(jobId)}`, { cookie });
    },
    cancelJob(jobId, cookie = '') {
      return request('POST', `/api/inference/jobs/${encodeURIComponent(jobId)}/cancel`, { cookie });
    },
    getCapabilities(cookie = '') {
      return request('GET', '/api/inference/capabilities', { cookie });
    },
  };
}


export const inferenceBackend = createInferenceBackend();
