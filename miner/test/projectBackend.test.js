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

function delayedFetch() {
  const calls = [];
  const fetch = (url, options) => new Promise((resolve, reject) => {
    calls.push({ url: String(url), options, resolve, reject });
  });
  const respond = (index, items = [], status = 200) => calls[index].resolve(
    new Response(JSON.stringify({ data: { items } }), { status }),
  );
  return { fetch, calls, respond };
}

test('identical authenticated project reads share only the unfinished request', async () => {
  const originalFetch = global.fetch;
  const upstream = delayedFetch();
  global.fetch = upstream.fetch;
  try {
    const first = projectApi.listProjects({ name: '', page: 1 }, 'session=one');
    const second = projectApi.listProjects({ page: '1', name: null }, 'session=one');
    const shared = upstream.calls.length;
    upstream.calls.forEach((_, index) => upstream.respond(index, [{ id: 7 }]));
    assert.deepEqual(await first, await second);
    assert.equal(shared, 1);
    const fresh = projectApi.listProjects({ page: 1 }, 'session=one');
    assert.equal(upstream.calls.length, 2);
    upstream.respond(1, [{ id: 8 }]);
    assert.deepEqual((await fresh).body.data.items, [{ id: 8 }]);
  } finally {
    global.fetch = originalFetch;
  }
});

test('project reads isolate cookies and filters and bypass unauthenticated sharing', async () => {
  const originalFetch = global.fetch;
  const upstream = delayedFetch();
  global.fetch = upstream.fetch;
  try {
    const reads = [
      projectApi.listProjects({ name: 'mine' }, 'session=one'),
      projectApi.listProjects({ name: 'mine' }, 'session=two'),
      projectApi.listProjects({ name: 'other' }, 'session=one'),
      projectApi.listProjects({}, ''),
      projectApi.listProjects({}, ''),
    ];
    assert.equal(upstream.calls.length, 5);
    upstream.calls.forEach((_, index) => upstream.respond(index, [{ id: index }]));
    const values = await Promise.all(reads);
    assert.deepEqual(values.map(value => value.body.data.items[0].id), [0, 1, 2, 3, 4]);
  } finally {
    global.fetch = originalFetch;
  }
});

test('failed or rejected project reads are cleared so a retry reaches upstream', async () => {
  const originalFetch = global.fetch;
  const upstream = delayedFetch();
  global.fetch = upstream.fetch;
  try {
    const rejected = projectApi.listProjects({}, 'session=retry');
    const observed = assert.rejects(rejected, /timeout/);
    upstream.calls[0].reject(new Error('timeout'));
    await observed;
    const denied = projectApi.listProjects({}, 'session=retry');
    upstream.respond(1, [], 403);
    assert.equal((await denied).status, 403);
    const retry = projectApi.listProjects({}, 'session=retry');
    assert.equal(upstream.calls.length, 3);
    upstream.respond(2, [{ id: 9 }]);
    assert.equal((await retry).body.data.items[0].id, 9);
  } finally {
    global.fetch = originalFetch;
  }
});

test('a completed mutation detaches old reads without deleting a newer pending refresh', async () => {
  const originalFetch = global.fetch;
  const upstream = delayedFetch();
  global.fetch = upstream.fetch;
  try {
    const old = projectApi.listProjects({}, 'session=write');
    const mutation = projectApi.createProject({ name: 'new' }, 'session=write');
    upstream.respond(1);
    await mutation;
    const fresh = projectApi.listProjects({}, 'session=write');
    assert.equal(upstream.calls.length, 3);
    upstream.respond(0, [{ id: 1 }]);
    await old;
    const shared = projectApi.listProjects({}, 'session=write');
    const count = upstream.calls.length;
    upstream.calls.slice(2).forEach((_, index) => upstream.respond(index + 2, [{ id: 2 }]));
    assert.deepEqual(await fresh, await shared);
    assert.equal(count, 3);
  } finally {
    global.fetch = originalFetch;
  }
});
