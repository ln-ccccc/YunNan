export const INFERENCE_TERMINAL_STATUSES = new Set([
  'succeeded',
  'succeeded_with_fallback',
  'partial_failed',
  'failed',
  'cancelled',
]);


const defaultWait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));


export async function pollInferenceJob({
  jobId,
  getJob,
  onUpdate = () => {},
  intervalMs = 1000,
  wait = defaultWait,
}) {
  while (true) {
    const job = await getJob(jobId);
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
