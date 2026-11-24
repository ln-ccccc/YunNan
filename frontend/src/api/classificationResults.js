import axios from 'axios';

import global from '@/global';
import { redirectToLegacyLogin } from '@/utils/authRedirect';


function apiError(message, { status = null, details = null, response = null, kind = null } = {}) {
  const error = new Error(message || '分类成果请求失败');
  error.status = status;
  error.details = details;
  error.response = response;
  // 失败的类别，供调用方区分文案与处置（2026-11-25）：
  //   http      服务返回了错误状态（404 / 500 …），status 里有状态码
  //   business  HTTP 200 但业务码不是 0
  //   network   连接不上（断网、服务不可达、跨域被拦）
  //   timeout   请求超时
  error.kind = kind || (response || status !== null ? 'http' : 'network');
  return error;
}

const classificationClient = axios.create({
  baseURL: global.BASEURL,
  withCredentials: true,
});

classificationClient.interceptors.response.use(
  (response) => {
    if (response.config.responseType === 'blob') return response;

    const payload = response.data || {};
    if (payload.code === 401) {
      redirectToLegacyLogin('expired');
      return Promise.reject(apiError(payload.msg || '登录已失效', {
        status: response.status,
        details: payload?.data?.details || null,
        response,
      }));
    }
    if (payload.code !== 0) {
      return Promise.reject(apiError(payload.msg || '分类成果请求失败', {
        status: response.status,
        details: payload?.data?.details || null,
        response,
        kind: 'business',
      }));
    }
    return response;
  },
  (error) => {
    const response = error?.response || null;
    if (response?.status === 401) {
      redirectToLegacyLogin('expired');
    }
    return Promise.reject(apiError(
      response?.data?.msg || (response?.status === 401 ? '登录已失效' : '分类成果服务暂时不可用'),
      {
        status: response?.status || null,
        details: response?.data?.data?.details || null,
        response,
        // 没有响应体：axios 对超时给 ECONNABORTED，其余（断网、不可达、跨域）都是连接失败
        kind: response ? 'http' : (error?.code === 'ECONNABORTED' ? 'timeout' : 'network'),
      },
    ));
  },
);

function resultUrl(projectId, resultId, suffix = '') {
  return `/api/projects/${encodeURIComponent(projectId)}/classification-results/${encodeURIComponent(resultId)}${suffix}`;
}

export async function getClassificationResult(projectId, resultId) {
  const response = await classificationClient.get(resultUrl(projectId, resultId));
  return response.data.data;
}

export async function getClassificationRevisions(projectId, resultId) {
  // 版本列表很小且有界(最多几十条)。客户端其余请求默认不限时, 只有它需要超时:
  // 首屏不再等它(2026-11-23 A2), 但请求挂住会让版本面板一直停在"加载中…"
  const response = await classificationClient.get(resultUrl(projectId, resultId, '/revisions'), {
    timeout: 30000,
  });
  return response.data.data;
}

export async function saveClassificationRevision(projectId, resultId, payload) {
  const response = await classificationClient.post(
    resultUrl(projectId, resultId, '/revisions'),
    payload,
  );
  return response.data.data;
}

export async function exportClassificationResult(projectId, resultId) {
  return classificationClient.post(
    resultUrl(projectId, resultId, '/export'),
    null,
    { responseType: 'blob' },
  );
}

// ── 编辑期几何接口（2026-11-24）──────────────────────────────────────────────
// 合法性判定、简化、合并全部在后端几何引擎（Shapely/GEOS）里做，与保存共用同一个判定函数：
// 「编辑时提示会被拒绝」与「保存时确实被拒绝」是同一件事。前端只发请求、展示结论，
// 不再自带空间关系 / 有效性 / 简化 / 合并的几何算法。三个接口都只读，不产生修订。
// 请求失败由拦截器转成 reject：error.status / error.details（code、field、reason…）可用，
// error.kind 说明是哪一类失败（http / business / network / timeout）。

/**
 * 请求体只带几何本身。后端要求每个几何恰好是 { type, coordinates }——不带 Feature
 * 外壳、properties 或别的键，多一个键就 422。
 */
function geometriesPayload(geometries) {
  return {
    geometries: geometries.map((geometry) => ({ type: geometry?.type, coordinates: geometry?.coordinates })),
  };
}

/**
 * 批量校验 1 ~ 2000 个面：逐个给出与保存完全相同的结论，与入参同序。
 * 每项为 { ok: true } 或 { ok: false, code: 'INVALID_GEOMETRY' | 'OUTSIDE_ROI', field, reason, … }。
 *
 * @returns {Promise<{ items: object[] }>}
 */
export async function validateClassificationGeometries(projectId, resultId, geometries) {
  const response = await classificationClient.post(
    resultUrl(projectId, resultId, '/geometry/validate'),
    geometriesPayload(geometries),
    // 请求挂住会让面一直停在「校验中」：校验是轻请求，15 秒没回来就按超时记为「未确认」
    { timeout: 15000 },
  );
  return response.data.data;
}

/**
 * 批量简化 1 ~ 2000 个面。容差由后端按这条成果的影像像元与每个图斑自己的面积决定（前端不传），
 * 返回的结果一定通过了保存同款校验和精度门槛。
 * items 与入参同序：{ outcome: 'changed', geometry, tolerance, area_change, shape_change } |
 * { outcome: 'unchanged' } | { outcome: 'rejected', reason }。只有 changed 带几何。
 *
 * @returns {Promise<{ mode: string, tolerance: number|null, pixel_size_m: number|null, items: object[] }>}
 */
export async function simplifyClassificationGeometries(projectId, resultId, geometries) {
  const response = await classificationClient.post(
    resultUrl(projectId, resultId, '/geometry/simplify'),
    geometriesPayload(geometries),
    // 请求挂住会让按钮一直转圈：简化 / 合并要算大面，给到一分钟
    { timeout: 60000 },
  );
  return response.data.data;
}

/**
 * 合并 2 ~ 50 个面。无法合并（不相交、几何无效、合并后越界…）时 reject：
 * error.status 为 422，error.message 为「几何无法合并」，error.details 带 code / field / reason。
 *
 * @returns {Promise<{ geometry: object }>}
 */
export async function unionClassificationGeometries(projectId, resultId, geometries) {
  const response = await classificationClient.post(
    resultUrl(projectId, resultId, '/geometry/union'),
    geometriesPayload(geometries),
    // 请求挂住会让按钮一直转圈：校验是轻请求，简化 / 合并要算大面，给到一分钟
    { timeout: 60000 },
  );
  return response.data.data;
}
