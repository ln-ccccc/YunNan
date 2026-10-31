import assert from 'node:assert/strict';
import test from 'node:test';

import { resolveForwardTarget } from '../services/basemapUpload.js';

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
