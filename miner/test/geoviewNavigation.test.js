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
