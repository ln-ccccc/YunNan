import assert from 'node:assert/strict';
import test from 'node:test';

import { buildChangeMatrixAssetPath } from '../services/changeMatrixAssets.js';

test('buildChangeMatrixAssetPath returns a same-origin relative path', () => {
  assert.equal(
    buildChangeMatrixAssetPath(11191, '11191_old.png'),
    '/change-matrix-outputs/11191/11191_old.png',
  );
});

test('buildChangeMatrixAssetPath rejects empty inputs', () => {
  assert.equal(buildChangeMatrixAssetPath('', '11191_old.png'), null);
  assert.equal(buildChangeMatrixAssetPath(11191, ''), null);
});
