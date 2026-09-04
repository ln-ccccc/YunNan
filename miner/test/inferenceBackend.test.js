import assert from 'node:assert/strict';
import test from 'node:test';

import { createInferenceBackend } from '../services/inferenceBackend.js';


function jsonResponse(status, body) {
  return {
    status,
    async text() {
      return JSON.stringify(body);
    },
  };
}


test('createInferenceJob forwards payload and cookie to Flask', async () => {
  const calls = [];
  const client = createInferenceBackend({
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      return jsonResponse(201, { success: true, data: { id: 'job-1', status: 'queued' } });
    },
    backendBaseUrl: 'http://backend:5008',
  });

  const result = await client.createJob({ old_tif_path: '/data/a.tif' }, 'session=x');

  assert.equal(result.status, 201);
  assert.equal(calls[0].url, 'http://backend:5008/api/inference/jobs');
  assert.equal(calls[0].options.headers.cookie, 'session=x');
  assert.deepEqual(JSON.parse(calls[0].options.body), { old_tif_path: '/data/a.tif' });
});
