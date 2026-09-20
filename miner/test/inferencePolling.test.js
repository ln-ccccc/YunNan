import assert from 'node:assert/strict';
import test from 'node:test';

import { pollInferenceJob } from '../src/services/inferencePolling.js';


test('pollInferenceJob waits until a terminal job status', async () => {
  const states = [
    { id: 'job-1', status: 'queued' },
    { id: 'job-1', status: 'running' },
    { id: 'job-1', status: 'succeeded', result: { written_fids: 2 } },
  ];
  let calls = 0;

  const result = await pollInferenceJob({
    jobId: 'job-1',
    getJob: async () => states[calls++],
    wait: async () => {},
    intervalMs: 0,
  });

  assert.equal(calls, 3);
  assert.equal(result.status, 'succeeded');
  assert.equal(result.result.written_fids, 2);
});

test('pollInferenceJob survives transient network failures (断线自愈)', async () => {
  // 前两次网络抖动，第三次恢复并返回终态——轮询不应被单次失败打死
  const networkError = new Error('Network Error');
  const states = [
    networkError,
    networkError,
    { id: 'job-1', status: 'succeeded' },
  ];
  let calls = 0;
  let waits = 0;

  const result = await pollInferenceJob({
    jobId: 'job-1',
    getJob: async () => {
      const next = states[calls++];
      if (next instanceof Error) throw next;
      return next;
    },
    wait: async () => { waits += 1; },
    intervalMs: 0,
  });

  assert.equal(calls, 3);
  assert.ok(waits >= 2, '失败后应等待重试而不是立刻抛出');
  assert.equal(result.status, 'succeeded');
});

test('pollInferenceJob gives up after consecutive failures exceed the budget', async () => {
  let calls = 0;
  const wait = async () => {};

  await assert.rejects(
    pollInferenceJob({
      jobId: 'job-1',
      getJob: async () => { calls += 1; throw new Error('Network Error'); },
      wait,
      intervalMs: 0,
      maxConsecutiveFailures: 3,
    }),
    (error) => {
      assert.match(error.message, /连续 3 次失败/);
      assert.equal(error.disconnect, true);
      return true;
    },
  );
  assert.equal(calls, 3);
});

test('pollInferenceJob treats 4xx as fatal without retry', async () => {
  const notFound = new Error('Request failed with status code 404');
  notFound.response = { status: 404 };
  let calls = 0;

  await assert.rejects(
    pollInferenceJob({
      jobId: 'missing',
      getJob: async () => { calls += 1; throw notFound; },
      wait: async () => {},
      intervalMs: 0,
      maxConsecutiveFailures: 10,
    }),
    (error) => error === notFound,
  );
  assert.equal(calls, 1, '4xx 不应重试');
});
