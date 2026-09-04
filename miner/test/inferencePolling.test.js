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
