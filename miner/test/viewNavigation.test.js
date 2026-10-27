import assert from 'node:assert/strict';
import test from 'node:test';

import {
  NAV_ITEMS,
  PRIMARY_NAV_ITEMS,
  TOOL_NAV_ITEMS,
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
  // 诚实施标：M5.4 起七大模块全部转正（数据管理=导出与配置快照中心）
  for (const key of ['projects', 'imagery', 'interpretation', 'editing', 'data', 'search', 'settings']) {
    assert.equal(NAV_ITEMS.find((item) => item.key === key).implemented, true, key);
  }
  assert.equal(NAV_ITEMS.filter((item) => !item.implemented).length, 0);
});

test('map hash keeps the project context', () => {
  assert.equal(buildMapHash(12), '#/map/12');
  assert.equal(parseProjectIdFromHash('#/map/12'), 12);
  assert.equal(parseProjectIdFromHash('#/map'), null);
  assert.equal(parseProjectIdFromHash('#/map/abc'), null);
});

// 2026-10-26 布局收敛：侧栏三主入口 + 其余六模块收进"更多工具"组
test('PRIMARY_NAV_ITEMS holds exactly the three main entries without static hashes', () => {
  assert.deepEqual(PRIMARY_NAV_ITEMS.map((item) => item.key), ['projects', 'map', 'geoview']);
  for (const item of PRIMARY_NAV_ITEMS) {
    assert.equal(item.hash, undefined, `${item.key} 主入口不带静态 hash`);
    assert.equal(item.implemented, true, item.key);
  }
  // projects 主入口与七模块表同源（label/implemented 单一事实来源）
  const projectsEntry = NAV_ITEMS.find((item) => item.key === 'projects');
  assert.equal(PRIMARY_NAV_ITEMS[0].key, projectsEntry.key);
  assert.equal(PRIMARY_NAV_ITEMS[0].label, projectsEntry.label);
});

test('TOOL_NAV_ITEMS is the remaining six modules referencing NAV_ITEMS entries', () => {
  assert.deepEqual(TOOL_NAV_ITEMS.map((item) => item.key), [
    'imagery', 'interpretation', 'editing', 'data', 'search', 'settings',
  ]);
  for (const item of TOOL_NAV_ITEMS) {
    const entry = NAV_ITEMS.find((candidate) => candidate.key === item.key);
    assert.equal(item, entry, `${item.key} 工具项必须引用 NAV_ITEMS 同一对象`);
    assert.equal(VIEW_HASH[item.key], item.hash);
  }
});
