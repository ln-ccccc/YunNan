export const INFERENCE_TERMINAL_STATUSES = new Set([
  'succeeded',
  'succeeded_with_fallback',
  'partial_failed',
  'failed',
  'cancelled',
]);


const defaultWait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));


// 断线自愈（江西 7f4e4d0 同款）：瞬时网络抖动不终止轮询，连续失败约 2 分钟
// （120 次 × 1s）才按断链处理——后端任务仍在执行，一次断网不打死页面
const DEFAULT_MAX_CONSECUTIVE_FAILURES = 120;

function isFatalPollError(error) {
  // 4xx 是确定的客户端侧错误（鉴权失效/任务不存在），重试无意义
  const status = error?.response?.status;
  return typeof status === 'number' && status >= 400 && status < 500;
}

export async function pollInferenceJob({
  jobId,
  getJob,
  onUpdate = () => {},
  intervalMs = 1000,
  wait = defaultWait,
  isCancelled = () => false,
  maxConsecutiveFailures = DEFAULT_MAX_CONSECUTIVE_FAILURES,
}) {
  let consecutiveFailures = 0;
  while (true) {
    if (isCancelled()) {
      const error = new Error('推理轮询已取消');
      error.cancelled = true;
      throw error;
    }
    let job;
    try {
      job = await getJob(jobId);
      consecutiveFailures = 0;
    } catch (error) {
      if (isCancelled() || isFatalPollError(error)) {
        throw error;
      }
      consecutiveFailures += 1;
      if (consecutiveFailures >= maxConsecutiveFailures) {
        const wrapped = new Error(`推理任务查询连续 ${consecutiveFailures} 次失败，已停止等待`);
        wrapped.cause = error;
        wrapped.disconnect = true;
        throw wrapped;
      }
      await wait(intervalMs);
      continue;
    }
    if (!job || !job.status) {
      throw new Error('推理任务查询未返回有效状态');
    }
    onUpdate(job);
    if (INFERENCE_TERMINAL_STATUSES.has(job.status)) {
      return job;
    }
    await wait(intervalMs);
  }
}
