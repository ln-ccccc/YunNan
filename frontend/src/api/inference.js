import { request } from "@/api/request.js";
import {
  INFERENCE_TERMINAL_STATUSES,
  MAX_POLL_ATTEMPTS,
  MAX_POLL_CONSECUTIVE_FAILURES,
  waitForInferenceJob as pollInferenceJob,
} from "@/utils/inferencePolling.mjs";

// 推理任务 API 层：状态查询、取消、轮询编排（借鉴江西 api/upload.js 的收编方式，
// 轮询不再散落在视图工具里）。轮询语义在 utils/inferencePolling.mjs（纯逻辑可单测）。
export { INFERENCE_TERMINAL_STATUSES, MAX_POLL_ATTEMPTS, MAX_POLL_CONSECUTIVE_FAILURES };

export function getInferenceJob(jobId) {
  return request({
    method: 'GET',
    url: `/api/inference/jobs/${encodeURIComponent(jobId)}`,
    // 推理轮询每秒一次：不触发全屏 loading 锁死页面（AGENTS §10 页面可继续操作）
    silent: true,
  });
}

export function cancelInferenceJob(jobId) {
  return request({
    method: 'POST',
    url: `/api/inference/jobs/${encodeURIComponent(jobId)}/cancel`,
    silent: true,
  });
}

export function waitForInferenceJob(jobId, options = {}) {
  return pollInferenceJob(jobId, { fetchJob: getInferenceJob, ...options });
}
