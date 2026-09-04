import test from 'node:test';
import assert from 'node:assert/strict';

import {
  buildClassificationResultRoute,
  readClassificationResultContext,
} from '../src/utils/classificationResultContext.mjs';

test('readClassificationResultContext accepts one positive safe project and result id', () => {
  assert.deepEqual(
    readClassificationResultContext('?project_id=17&result_id=29'),
    { projectId: 17, resultId: 29 },
  );
});

test('readClassificationResultContext rejects missing, ambiguous, and invalid ids', () => {
  const invalidSearches = [
    '',
    '?project_id=17',
    '?result_id=29',
    '?project_id=0&result_id=29',
    '?project_id=-1&result_id=29',
    '?project_id=17&result_id=0',
    '?project_id=17&result_id=7.0',
    '?project_id=17&result_id=7e0',
    '?project_id=17&result_id=not-a-number',
    '?project_id=17&project_id=18&result_id=29',
    '?project_id=17&result_id=29&result_id=30',
    '?project_id=9007199254740992&result_id=29',
    '?project_id=17&result_id=9007199254740992',
  ];

  invalidSearches.forEach((search) => {
    assert.equal(readClassificationResultContext(search), null, search);
  });
});

test('buildClassificationResultRoute emits the editor path only for valid ids', () => {
  assert.equal(
    buildClassificationResultRoute(17, '29'),
    '/classification-results/editor?project_id=17&result_id=29',
  );
  assert.equal(buildClassificationResultRoute(0, 29), null);
  assert.equal(buildClassificationResultRoute(17, '7e0'), null);
  assert.equal(buildClassificationResultRoute(17, 9007199254740992), null);
});
