import assert from 'node:assert/strict';
import test from 'node:test';

import {
  NAV_ITEMS,
  VIEW_HASH,
  buildMapHash,
  parseProjectIdFromHash,
  resolveViewFromHash,
} from '../src/navigation/viewNavigation.js';

test('resolveViewFromHash returns map only when a project id is present', () => {
  assert.equal(resolveViewFromHash('#/map/12'), 'map');
  assert.equal(resolveViewFromHash('#/map'), 'projects');
  assert.equal(resolveViewFromHash('#/projects'), 'projects');
  assert.equal(resolveViewFromHash(''), 'projects');
  assert.equal(resolveViewFromHash('#/unknown'), 'projects');
});

test('VIEW_HASH exposes one hash per nav module (M1 console routing)', () => {
  assert.deepEqual(VIEW_HASH, {
    projects: '#/projects',
    imagery: '#/imagery',
    interpretation: '#/interpretation',
    editing: '#/editing',
    data: '#/data',
    search: '#/search',
    settings: '#/settings',
  });
});

test('every static module hash resolves to its own view', () => {
  for (const key of Object.keys(VIEW_HASH)) {
    assert.equal(resolveViewFromHash(VIEW_HASH[key]), key, VIEW_HASH[key]);
  }
  assert.equal(resolveViewFromHash('#/map/3'), 'map');
  assert.equal(resolveViewFromHash('#/nope'), 'projects');
});

test('NAV_ITEMS covers the seven modules with unique keys and hashes', () => {
  assert.equal(NAV_ITEMS.length, 7);
  const keys = NAV_ITEMS.map((item) => item.key);
  const hashes = NAV_ITEMS.map((item) => item.hash);
  assert.equal(new Set(keys).size, 7);
  assert.equal(new Set(hashes).size, 7);
  for (const item of NAV_ITEMS) {
    assert.equal(VIEW_HASH[item.key], item.hash);
    assert.equal(typeof item.label, 'string');
    assert.equal(typeof item.implemented, 'boolean');
  }
  // 诚实施标：M1 项目管理 + M3 图斑编辑落地，其余五模块占位（M4 填充后翻转）
  assert.equal(NAV_ITEMS.find((item) => item.key === 'projects').implemented, true);
  assert.equal(NAV_ITEMS.find((item) => item.key === 'editing').implemented, true);
  assert.equal(NAV_ITEMS.filter((item) => !item.implemented).length, 5);
});

test('map hash keeps the project context', () => {
  assert.equal(buildMapHash(12), '#/map/12');
  assert.equal(parseProjectIdFromHash('#/map/12'), 12);
  assert.equal(parseProjectIdFromHash('#/map'), null);
  assert.equal(parseProjectIdFromHash('#/map/abc'), null);
});
