import test from 'node:test';
import assert from 'node:assert/strict';

import {
  LIFECYCLE_STATUS_LABELS,
  formatLifecycleStatus,
} from '../src/utils/projectStatusLabels.js';

import {
  getSelectedProjectId,
  setSelectedProjectId,
  subscribeProjectSelection,
  _resetForTest,
} from '../src/services/projectSelectionStore.js';

test('lifecycle status labels cover the four persisted values', () => {
  assert.equal(formatLifecycleStatus('draft'), '草稿');
  assert.equal(formatLifecycleStatus('active'), '进行中');
  assert.equal(formatLifecycleStatus('completed'), '已完成');
  assert.equal(formatLifecycleStatus('archived'), '已归档');
  assert.equal(LIFECYCLE_STATUS_LABELS.active, '进行中');
});

test('lifecycle status fallback keeps raw value then placeholder', () => {
  assert.equal(formatLifecycleStatus('custom'), 'custom');
  assert.equal(formatLifecycleStatus(''), '--');
  assert.equal(formatLifecycleStatus(undefined), '--');
});

test('project selection store normalizes ids and notifies subscribers', () => {
  _resetForTest();
  assert.equal(getSelectedProjectId(), null);

  const seen = [];
  const unsubscribe = subscribeProjectSelection((id) => seen.push(id));

  setSelectedProjectId('7');
  assert.equal(getSelectedProjectId(), 7);
  setSelectedProjectId(-3); // 非法值归一化为 null
  assert.equal(getSelectedProjectId(), null);
  setSelectedProjectId(0);
  setSelectedProjectId(1.5);
  assert.equal(getSelectedProjectId(), null);
  setSelectedProjectId(12);
  setSelectedProjectId(12); // 重复写入不重复通知
  assert.deepEqual(seen, [7, null, 12]);

  unsubscribe();
  setSelectedProjectId(30);
  assert.deepEqual(seen, [7, null, 12]);
  _resetForTest();
});

test('project selection store survives without window.localStorage', () => {
  _resetForTest();
  // node --test 环境无 window：内存态必须照常工作且不抛错
  setSelectedProjectId(5);
  assert.equal(getSelectedProjectId(), 5);
  const notified = [];
  subscribeProjectSelection((id) => notified.push(id));
  setSelectedProjectId(null);
  assert.deepEqual(notified, [null]);
  _resetForTest();
});

test('project selection store persists via localStorage when available', () => {
  const saved = {};
  const fakeStorage = {
    getItem: (k) => (k in saved ? saved[k] : null),
    setItem: (k, v) => { saved[k] = String(v); },
    removeItem: (k) => { delete saved[k]; },
  };
  globalThis.window = { localStorage: fakeStorage };
  try {
    _resetForTest();
    setSelectedProjectId(9);
    assert.equal(saved['miner.selected-project-id'], '9');
    setSelectedProjectId(null);
    assert.equal('miner.selected-project-id' in saved, false);
  } finally {
    delete globalThis.window;
    _resetForTest();
  }
});
