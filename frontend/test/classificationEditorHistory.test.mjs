import assert from 'node:assert/strict';
import test from 'node:test';

import { createEditorHistory } from '../src/utils/classificationEditorHistory.mjs';

test('record pushes the pre-gesture state and redo is cleared on new edits', () => {
  const history = createEditorHistory();
  history.reset('A');
  assert.equal(history.canUndo, false);
  assert.equal(history.isDirty('A'), false);

  // A → B：入栈的是手势前状态 A
  assert.equal(history.record('create', 'B', 1000), true);
  assert.equal(history.canUndo, true);
  assert.equal(history.isDirty('B'), true);

  // B → C：新的编辑清空 redo
  history.record('delete', 'C', 1100);
  const back = history.undo('C');
  assert.equal(back, 'B');
  assert.equal(history.canRedo, true);
  history.record('properties', 'D', 1200);
  assert.equal(history.canRedo, false);
});

test('consecutive vertex gestures within the window coalesce into one entry', () => {
  const history = createEditorHistory();
  history.reset('A');
  history.record('vertex', 'B', 1000);
  assert.equal(history.record('vertex', 'C', 1300), false);
  assert.equal(history.record('vertex', 'D', 1499), false);
  assert.equal(history.canUndo, true);
  // 撤销一次直接回到手势前 A
  assert.equal(history.undo('D'), 'A');
  assert.equal(history.canUndo, false);
});

test('first vertex always records even at window start', () => {
  const history = createEditorHistory();
  history.reset('A');
  // 栈空时首条 vertex 必须入栈，否则首段拖拽不可撤销
  assert.equal(history.record('vertex', 'B', 1000), true);
  assert.equal(history.canUndo, true);
});

test('vertex gestures outside the window record separately', () => {
  const history = createEditorHistory();
  history.reset('A');
  history.record('vertex', 'B', 1000);
  history.record('vertex', 'C', 2000);
  assert.equal(history.undo('C'), 'B');
  assert.equal(history.undo('B'), 'A');
});

test('identical state does not record', () => {
  const history = createEditorHistory();
  history.reset('A');
  assert.equal(history.record('properties', 'A', 1000), false);
  assert.equal(history.canUndo, false);
});

test('undo/redo are symmetric and restore both directions', () => {
  const history = createEditorHistory();
  history.reset('A');
  history.record('create', 'B', 1000);
  history.record('delete', 'C', 1100);

  const back1 = history.undo('C');
  assert.equal(back1, 'B');
  assert.equal(history.canUndo, true);
  assert.equal(history.canRedo, true);

  const back2 = history.undo('B');
  assert.equal(back2, 'A');
  assert.equal(history.canUndo, false);

  assert.equal(history.redo('A'), 'B');
  assert.equal(history.redo('B'), 'C');
  assert.equal(history.canRedo, false);
  assert.equal(history.isDirty('C'), true);
  assert.equal(history.isDirty('A'), false);
});

test('undo/redo on empty stacks return null', () => {
  const history = createEditorHistory();
  history.reset('A');
  assert.equal(history.undo('A'), null);
  assert.equal(history.redo('A'), null);
});

test('stack depth is capped at the limit, dropping the oldest', () => {
  const history = createEditorHistory({ limit: 3 });
  history.reset('V0');
  history.record('create', 'V1', 1000);
  history.record('create', 'V2', 1100);
  history.record('create', 'V3', 1200);
  history.record('create', 'V4', 1300);
  // 丢掉了 V0：连续撤销三次后应停在 V1，再无更旧可退
  assert.equal(history.undo('V4'), 'V3');
  assert.equal(history.undo('V3'), 'V2');
  assert.equal(history.undo('V2'), 'V1');
  assert.equal(history.canUndo, false);
});

test('reset clears both stacks and rebases dirty state', () => {
  const history = createEditorHistory();
  history.reset('A');
  history.record('create', 'B', 1000);
  history.reset('NEW');
  assert.equal(history.canUndo, false);
  assert.equal(history.canRedo, false);
  assert.equal(history.isDirty('NEW'), false);
  assert.equal(history.isDirty('A'), true);
});
