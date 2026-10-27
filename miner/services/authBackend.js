const backendBaseUrl = (process.env.GEOVIEW_BACKEND_URL || 'http://localhost:5008').replace(/\/$/, '');

// backend 重启窗口（等 MySQL+初始化约 40s）内连接被拒/重置是瞬态：单次短重试
// 消化绝大多数窗口，剩余才以 502 暴露给前端（2026-10-27 重启时序实测）
const TRANSIENT_CONNECTION_CODES = new Set(['ECONNRESET', 'ECONNREFUSED', 'ETIMEDOUT', 'EPIPE']);

function isTransientConnectionError(error) {
  if (!(error instanceof TypeError)) return false;
  const cause = error?.cause;
  return !cause || TRANSIENT_CONNECTION_CODES.has(cause?.code);
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function requestBackendAuth(method, path, { body, cookie, timeoutMs = 15000 } = {}) {
  const attempt = () =>
    fetch(`${backendBaseUrl}${path}`, {
      method,
      headers: {
        ...(body ? { 'content-type': 'application/json' } : {}),
        ...(cookie ? { cookie } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(timeoutMs),
    }).then(async (response) => {
      const text = await response.text();
      let parsed;
      try {
        parsed = text ? JSON.parse(text) : {};
      } catch (_) {
        parsed = { success: false, code: 1, msg: text || 'Invalid JSON response' };
      }
      return {
        status: response.status,
        body: parsed,
        setCookies: typeof response.headers.getSetCookie === 'function'
          ? response.headers.getSetCookie()
          : (response.headers.get('set-cookie') ? [response.headers.get('set-cookie')] : []),
      };
    });

  try {
    return await attempt();
  } catch (error) {
    if (isTransientConnectionError(error)) {
      await delay(300);
      return attempt();
    }
    throw error;
  }
}

export const authBackend = {
  login(payload) {
    return requestBackendAuth('POST', '/api/auth/login', { body: payload });
  },
  logout(cookie = '') {
    return requestBackendAuth('POST', '/api/auth/logout', { cookie });
  },
  changePassword(payload, cookie = '') {
    return requestBackendAuth('POST', '/api/auth/change-password', { body: payload, cookie });
  },
  session(cookie = '') {
    // session 检查在每个受保护请求上执行：5s 超时足够，避免上游挂起时长时间占用请求线程
    return requestBackendAuth('GET', '/api/auth/session', { cookie, timeoutMs: 5000 });
  },
};
