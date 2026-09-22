// 推理任务轮询纯逻辑（不依赖 axios/element-plus，node --test 可直接测）。
// HTTP 编线（getInferenceJob/cancelInferenceJob）在 api/inference.js，注入 fetchJob 复用本实现。
export const INFERENCE_TERMINAL_STATUSES = new Set([
  'succeeded',
  'succeeded_with_fallback',
  'partial_failed',
  'failed',
  'cancelled',
]);

// 轮询上限：与后端 INFERENCE_JOB_TIMEOUT_SECONDS（默认 3600s）对齐并留裕量；
// 任务卡死在非终态时避免无限轮询
export const MAX_POLL_ATTEMPTS = 3700;

// 断线自愈（江西 7f4e4d0 同款）：瞬时网络抖动不终止轮询，连续失败约 2 分钟
// 才按断链处理——后端任务仍在执行，一次断网不打死流程
export const MAX_POLL_CONSECUTIVE_FAILURES = 120;

export const POLL_INTERVAL_MS = 1000;

const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

/**
 * 轮询直至任务进入终态（含 cancelled）。
 * - 4xx / 主动取消（kind=aborted）/ 业务失败（kind=backend）/ 登录失效（kind=auth）
 *   为确定性失败，立即抛出；
 * - 5xx / 网络错误按断线自愈阈值重试，超限抛带 disconnect 标记的错误；
 * - fetchJob 必须注入（返回与 axios response 同构 {data:{data:job}}）。
 */
export async function waitForInferenceJob(jobId, options = {}) {
  const {
    fetchJob,
    intervalMs = POLL_INTERVAL_MS,
    maxAttempts = MAX_POLL_ATTEMPTS,
    maxConsecutiveFailures = MAX_POLL_CONSECUTIVE_FAILURES,
  } = options;
  if (typeof fetchJob !== 'function') {
    throw new Error('waitForInferenceJob 需要 inject fetchJob');
  }

  let consecutiveFailures = 0;
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    let response;
    try {
      response = await fetchJob(jobId);
      consecutiveFailures = 0;
    } catch (error) {
      // request.js 包装后原始 axios 错误在 cause 上；两种形态都认
      const status = error?.response?.status ?? error?.cause?.response?.status;
      if (typeof status === 'number' && status >= 400 && status < 500) throw error;
      // 确定性失败不重试：主动取消、业务失败（code!==0）、登录失效
      if (error?.kind === 'aborted' || error?.kind === 'backend' || error?.kind === 'auth') throw error;
      consecutiveFailures += 1;
      if (consecutiveFailures >= maxConsecutiveFailures) {
        const wrapped = new Error('推理任务查询持续失败，连接可能已中断');
        wrapped.disconnect = true;
        throw wrapped;
      }
      await wait(intervalMs);
      continue;
    }
    const job = response?.data?.data;
    if (!job?.status) throw new Error('推理任务查询未返回有效状态');
    if (INFERENCE_TERMINAL_STATUSES.has(job.status)) return job;
    await wait(intervalMs);
  }
  throw new Error(`推理任务 ${jobId} 长时间未结束，已停止等待，请稍后在历史记录中查看`);
}
