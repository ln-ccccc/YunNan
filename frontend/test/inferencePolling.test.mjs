import assert from 'node:assert/strict';
import test from 'node:test';

import {
  INFERENCE_TERMINAL_STATUSES,
  waitForInferenceJob,
} from '../src/utils/inferencePolling.mjs';

const FAST = { intervalMs: 0, maxAttempts: 50, maxConsecutiveFailures: 3 };

function jobResponse(status) {
  return { data: { data: { id: 'j1', status } } };
}

test('waitForInferenceJob resolves on the first terminal status it observes', async () => {
  const seen = [];
  const job = await waitForInferenceJob('j1', {
    ...FAST,
    fetchJob: async () => {
      seen.push(1);
      return jobResponse(seen.length < 3 ? 'running' : 'succeeded');
    },
  });
  assert.equal(job.status, 'succeeded');
  assert.equal(seen.length, 3);
});

test('waitForInferenceJob observes user cancellation as a cancelled terminal state', async () => {
  // 取消是"请求后端标记 → 轮询观察到终态"，不是本地中断循环
  let polls = 0;
  const job = await waitForInferenceJob('j1', {
    ...FAST,
    fetchJob: async () => jobResponse(++polls < 2 ? 'running' : 'cancelled'),
  });
  assert.equal(job.status, 'cancelled');
  assert.equal(polls, 2);
});

test('waitForInferenceJob treats 4xx as fatal instead of retrying into silence', async () => {
  let polls = 0;
  const err404 = new Error('推理任务不存在');
  err404.response = { status: 404 };
  await assert.rejects(
    waitForInferenceJob('gone', { ...FAST, fetchJob: async () => { polls += 1; throw err404; } }),
    /推理任务不存在/,
  );
  assert.equal(polls, 1, '4xx 不应重试');
});

test('waitForInferenceJob survives transient failures then declares disconnect past the threshold', async () => {
  // 前两次抖动恢复，随后连续失败到阈值 → disconnect 标记
  let calls = 0;
  const blip = new Error('Network Error');
  blip.kind = 'network';
  await assert.rejects(
    waitForInferenceJob('j1', {
      ...FAST,
      fetchJob: async () => {
        calls += 1;
        if (calls <= 2) throw blip;
        if (calls === 3) return jobResponse('running');
        throw blip;
      },
    }),
    (error) => {
      assert.equal(error.disconnect, true);
      assert.match(error.message, /连接可能已中断/);
      return true;
    },
  );
});

test('waitForInferenceJob stops after maxAttempts for a job stuck in a non-terminal state', async () => {
  let polls = 0;
  await assert.rejects(
    waitForInferenceJob('stuck', {
      intervalMs: 0,
      maxAttempts: 4,
      fetchJob: async () => { polls += 1; return jobResponse('running'); },
    }),
    /长时间未结束/,
  );
  assert.equal(polls, 4);
});

test('waitForInferenceJob throws on malformed payload without a status field', async () => {
  await assert.rejects(
    waitForInferenceJob('bad', { ...FAST, fetchJob: async () => ({ data: { data: {} } }) }),
    /未返回有效状态/,
  );
});

test('terminal status set keeps the worker-observable contract', () => {
  // 与后端 worker 终态（applications/inference/status.py TERMINAL_STATUSES）对齐
  for (const status of ['succeeded', 'succeeded_with_fallback', 'partial_failed', 'failed', 'cancelled']) {
    assert.equal(INFERENCE_TERMINAL_STATUSES.has(status), true, `缺终态 ${status}`);
  }
  assert.equal(INFERENCE_TERMINAL_STATUSES.has('running'), false);
  assert.equal(INFERENCE_TERMINAL_STATUSES.has('queued'), false);
});
