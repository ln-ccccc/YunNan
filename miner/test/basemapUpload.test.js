import assert from 'node:assert/strict';
import http from 'node:http';
import test from 'node:test';
import { Readable } from 'node:stream';

import { forwardRawStream, resolveForwardTarget } from '../services/basemapUpload.js';

test('forward target encodes the filename and pins the authoritative backend endpoint', () => {
  const target = resolveForwardTarget('底图 2024.tif', 10, 'http://backend:5008');
  assert.equal(
    target,
    'http://backend:5008/api/projects/10/spatial/basemap-upload?filename=%E5%BA%95%E5%9B%BE%202024.tif',
  );
});

test('forward target never lets a filename escape the query parameter', () => {
  const target = resolveForwardTarget('../../etc/passwd.tif', 10, 'http://backend:5008');
  // 路径穿越片段必须被 encodeURIComponent 吞进 query，不得出现在路径部分
  assert.ok(target.startsWith('http://backend:5008/api/projects/10/spatial/basemap-upload?filename='));
  assert.ok(!target.includes('/etc/passwd.tif') || target.indexOf('/etc/') > target.indexOf('?filename='));
});

test('forwardRawStream relays the chunk digest header (丢头 = 内容校验静默失效)', async () => {
  const seen = { headers: null };
  const server = http.createServer((req, res) => {
    seen.headers = req.headers;
    req.resume();
    req.on('end', () => {
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end('{"code":0,"success":true}');
    });
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;

  try {
    const settled = new Promise((resolve) => {
      const fakeRes = {
        status(code) { this.statusCode = code; return this; },
        set() { return this; },
        send(body) { resolve({ statusCode: this.statusCode, body }); return this; },
        json(payload) { resolve({ statusCode: this.statusCode, body: payload }); return this; },
        headersSent: false,
      };
      const fakeReq = Readable.from(['chunk-body-bytes']);
      fakeReq.headers = {
        'content-type': 'application/octet-stream',
        'content-length': '16',
        cookie: 'session=abc',
        'x-chunk-sha256': 'deadbeef01',
      };
      forwardRawStream(fakeReq, fakeRes, {
        backendOrigin: `http://127.0.0.1:${port}`,
        path: '/api/projects/10/spatial/basemap-upload/chunk/s1/0',
      });
    });
    const outcome = await settled;
    assert.equal(outcome.statusCode, 200);
    // 摘要头必须原样到达后端——write_chunk 靠它做分块内容校验
    assert.equal(seen.headers['x-chunk-sha256'], 'deadbeef01');
    assert.equal(seen.headers.cookie, 'session=abc');
    assert.equal(seen.headers['content-type'], 'application/octet-stream');
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});
