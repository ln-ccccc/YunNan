import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';

const require = createRequire(import.meta.url);
const axios = require('axios');
const loadingSource = fs.readFileSync(new URL('../src/utils/loading.js', import.meta.url), 'utf8')
  .replace(/^import[\s\S]*?;\s*/gm, '').replaceAll('export function ', 'function ')
  .replace(/export default[\s\S]*$/, 'return { showFullScreenLoading, hideFullScreenLoading };');
const requestSource = fs.readFileSync(new URL('../src/api/request.js', import.meta.url), 'utf8')
  .replace(/^import[\s\S]*?;\s*/gm, '').replace('export function request', 'function request');

function harness() {
  let opens = 0;
  let closes = 0;
  const loading = new Function('Loading', 'document', loadingSource)(
    () => { opens++; return { close: () => { closes++; } }; }, { querySelector: () => null },
  );
  const request = new Function('dependencies', `with (dependencies) { ${requestSource}; return request; }`)({
    axios, global: { BASEURL: 'http://localhost' }, ...loading,
    ElMessage: { error() {} }, redirectToLegacyLogin() {},
  });
  let finish;
  const foreground = request({ url: '/foreground', adapter: (config) => new Promise((resolve) => {
    finish = () => resolve({ config, status: 200, data: { code: 0 }, headers: {} });
  }) });
  return { request, foreground, finish: () => finish(), counts: () => ({ opens, closes }) };
}

test('a silent success cannot close another active request loading', async () => {
  const h = harness();
  await h.request({ url: '/poll', silent: true, adapter: async (config) => ({ config, status: 200, data: { code: 0 }, headers: {} }) });
  assert.deepEqual(h.counts(), { opens: 1, closes: 0 });
  h.finish();
  await h.foreground;
  assert.deepEqual(h.counts(), { opens: 1, closes: 1 });
});

for (const kind of ['http', 'network', 'aborted']) {
  test(`a silent ${kind} failure cannot close another active request loading`, async () => {
    const h = harness();
    await assert.rejects(h.request({ url: '/poll', silent: true, adapter: async (config) => {
      if (kind === 'aborted') throw new axios.Cancel('cancelled');
      throw Object.assign(new Error('failed'), { config, ...(kind === 'http' ? { response: { status: 503, data: { msg: 'failed' } } } : {}) });
    } }), (error) => error.kind === kind);
    assert.deepEqual(h.counts(), { opens: 1, closes: 0 });
    h.finish();
    await h.foreground;
    assert.deepEqual(h.counts(), { opens: 1, closes: 1 });
  });
}

test('foreground failures release only their own loading count', async () => {
  const h = harness();
  await assert.rejects(h.request({ url: '/failure', adapter: async (config) => {
    throw Object.assign(new Error('offline'), { config });
  } }));
  assert.deepEqual(h.counts(), { opens: 1, closes: 0 });
  h.finish();
  await h.foreground;
  assert.deepEqual(h.counts(), { opens: 1, closes: 1 });
});
