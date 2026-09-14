import assert from 'node:assert/strict';
import test from 'node:test';

import { projectApi } from '../services/projectBackend.js';


test('project backend proxies a validated inference image as binary', async () => {
  const originalFetch = global.fetch;
  const calls = [];
  global.fetch = async (url, options) => {
    calls.push({ url: String(url), options });
    return new Response(Buffer.from('project-image'), {
      status: 200,
      headers: { 'content-type': 'image/png' },
    });
  };

  try {
    const result = await projectApi.getProjectInferenceOutput('7', '101', '101+2022.png', 'session=x');
    assert.equal(result.status, 200);
    assert.equal(result.contentType, 'image/png');
    assert.equal(result.body.toString(), 'project-image');
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, 'http://localhost:5008/api/projects/7/outputs/inference/101/101%2B2022.png');
    assert.equal(calls[0].options.method, 'GET');
    assert.deepEqual(calls[0].options.headers, { cookie: 'session=x' });
    // BFF 出站请求必须带超时，避免上游挂起时无限占用请求线程
    assert.ok(calls[0].options.signal instanceof AbortSignal);
  } finally {
    global.fetch = originalFetch;
  }
});


test('project backend refuses unsafe inference output route segments before fetch', () => {
  assert.throws(
    () => projectApi.getProjectInferenceOutput('../admin', '101', '101+2022.png', 'session=x'),
    /项目参数不合法/,
  );
});
