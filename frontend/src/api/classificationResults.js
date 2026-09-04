import axios from 'axios';

import global from '@/global';
import { redirectToLegacyLogin } from '@/utils/authRedirect';


function apiError(message, { status = null, details = null, response = null } = {}) {
  const error = new Error(message || '分类成果请求失败');
  error.status = status;
  error.details = details;
  error.response = response;
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
  const response = await classificationClient.get(resultUrl(projectId, resultId, '/revisions'));
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
