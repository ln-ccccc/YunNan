import assert from 'node:assert/strict';
import test from 'node:test';

import { VIEW_HASH, buildMapHash, parseProjectIdFromHash, resolveViewFromHash } from '../src/navigation/viewNavigation.js';

test('resolveViewFromHash returns map only when a project id is present', () => {
  assert.equal(resolveViewFromHash('#/map/12'), 'map');
  assert.equal(resolveViewFromHash('#/map'), 'projects');
  assert.equal(resolveViewFromHash('#/projects'), 'projects');
  assert.equal(resolveViewFromHash(''), 'projects');
  assert.equal(resolveViewFromHash('#/unknown'), 'projects');
});

test('VIEW_HASH exposes stable project and map hashes', () => {
  assert.deepEqual(VIEW_HASH, {
    projects: '#/projects',
  });
});

test('map hash keeps the project context', () => {
  assert.equal(buildMapHash(12), '#/map/12');
  assert.equal(parseProjectIdFromHash('#/map/12'), 12);
  assert.equal(parseProjectIdFromHash('#/map'), null);
});
