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

  const payload = { project_id: 7, dataset_id: 12, year: '2024', device: 'auto' };
  const result = await client.createJob(payload, 'session=x');

  assert.equal(result.status, 201);
  assert.equal(calls[0].url, 'http://backend:5008/api/inference/jobs');
  assert.equal(calls[0].options.headers.cookie, 'session=x');
  assert.deepEqual(JSON.parse(calls[0].options.body), payload);
  assert.doesNotMatch(JSON.stringify(payload), /tif_path|kml_path|output_root|storage_key/);
});
