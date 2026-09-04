import test from 'node:test';
import assert from 'node:assert/strict';

import { buildBackendBaseUrl } from '../src/utils/backendUrl.mjs';

test('buildBackendBaseUrl uses the current browser hostname', () => {
  assert.equal(
    buildBackendBaseUrl({ protocol: 'http:', hostname: 'localhost', port: 5008 }),
    'http://localhost:5008/',
  );
  assert.equal(
    buildBackendBaseUrl({ protocol: 'http:', hostname: '192.168.1.20', port: 5008 }),
    'http://192.168.1.20:5008/',
  );
});
