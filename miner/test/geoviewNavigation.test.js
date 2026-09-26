import test from 'node:test';
import assert from 'node:assert/strict';

import { buildGeoViewUrl } from '../src/navigation/geoviewNavigation.js';

test('buildGeoViewUrl passes only the active project id', () => {
  const result = buildGeoViewUrl(
    'http://localhost:3000/#/detectchanges?project_id=999',
    {
      href: 'http://192.168.1.20:4000/#/projects/7',
      hostname: '192.168.1.20',
    },
    7,
  );

  assert.equal(result, 'http://192.168.1.20:3000/segmentation?project_id=7');
});

test('buildGeoViewUrl omits invalid project ids', () => {
  const result = buildGeoViewUrl(
    'http://localhost:3000/#/segmentation?project_id=999',
    {
      href: 'http://192.168.1.20:4000/#/projects',
      hostname: '192.168.1.20',
    },
    'dali',
  );

  assert.equal(result, 'http://192.168.1.20:3000/segmentation');
});

// M5.4：编辑器直跳地址——origin 归一防配置残留污染（2026-09-25 P1 实测）
test('buildGeoViewEditorUrl strips hash/path residue from the configured base', async () => {
  const { buildGeoViewEditorUrl } = await import('../src/navigation/geoviewNavigation.js');
  const location = { href: 'http://192.168.1.20:4000/#/interpretation' };
  const result = buildGeoViewEditorUrl(
    'http://localhost:3000/#/segmentation',
    location,
    1,
    27,
  );
  assert.equal(result, 'http://localhost:3000/classification-results/editor?project_id=1&result_id=27');
});

test('buildGeoViewEditorUrl omits result_id when absent or invalid', async () => {
  const { buildGeoViewEditorUrl } = await import('../src/navigation/geoviewNavigation.js');
  const location = { href: 'http://localhost:4000/#/data' };
  assert.equal(
    buildGeoViewEditorUrl('http://localhost:3000/', location, 3),
    'http://localhost:3000/classification-results/editor?project_id=3',
  );
  assert.equal(
    buildGeoViewEditorUrl('http://localhost:3000/', location, 3, 0),
    'http://localhost:3000/classification-results/editor?project_id=3',
  );
});
