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

// 保存竞态（2026-10-31 审计 P1）：保存请求发出时画布是 B，响应返回前又编辑到
// C。markSaved(B) 后服务端已落库 B，撤销不得穿越保存边界回到 A，也不得把已
// 落库的 B 判为未保存；保存期间的本地编辑 C 必须保留且仍可撤销。
test('markSaved rebases to the saved snapshot and keeps post-save gestures (A/B/C)', () => {
  const history = createEditorHistory();
  history.reset('A');
  history.record('create', 'B', 1000);
  history.record('properties', 'C', 1100);

  assert.equal(history.markSaved('B'), true);
  assert.equal(history.isDirty('C'), true, '保存期间的后续编辑相对新基线仍是脏的');
  assert.equal(history.isDirty('B'), false, '已落库的 B 不应再被判为未保存');

  // 撤销一次回到 B（不是 A），且不能再退
  assert.equal(history.undo('C'), 'B');
  assert.equal(history.canUndo, false, '撤销不得穿越保存边界');
  assert.equal(history.isDirty('B'), false);

  // 重做与继续编辑仍可用，且撤销到 B 为止
  assert.equal(history.redo('B'), 'C');
  assert.equal(history.isDirty('C'), true);
  history.record('vertex', 'D', 1500);
  assert.equal(history.undo('D'), 'C');
  assert.equal(history.undo('C'), 'B');
  assert.equal(history.canUndo, false);
});

test('markSaved clears redo and falls back when undo crossed the save snapshot', () => {
  const history = createEditorHistory();
  history.reset('A');
  history.record('create', 'B', 1000);
  history.record('delete', 'C', 1100);
  history.undo('C'); // 撤销到 B：B 已被弹出栈（栈存手势前快照），redoStack=[C]

  // 保存提交画布 B 落库：B 不在撤销栈中 → 退化清栈；跨保存边界的 redo
  // （回到 C）不得复活；基线 B 判脏正确，数据不丢
  assert.equal(history.markSaved('B'), false);
  assert.equal(history.canRedo, false, '保存边界另一侧的重做记录必须作废');
  assert.equal(history.canUndo, false);
  assert.equal(history.isDirty('B'), false, '已落库的 B 不应再被判为未保存');
});

// 保存提交边界（2026-10-31 复审 P2）：提交后 coalesce 窗口内的 vertex 手势
// 不得合并进提交前手势——否则栈中没有保存边界快照，markSaved 只能退化清栈，
// 保存期间的修改无法撤销（实测 A→B→提交B→100ms 内编辑C 时 canUndo=false）。
test('markSubmitBoundary forces the first post-submit gesture onto the stack', () => {
  const history = createEditorHistory();
  history.reset('A');
  history.record('vertex', 'B', 1000);
  history.markSubmitBoundary(); // 保存请求发出
  // 提交后 100ms 内的 vertex 编辑：若无边界会被 coalesce 吞掉
  assert.equal(history.record('vertex', 'C', 1100), true, '提交后的首个手势必须入栈');
  assert.equal(history.canUndo, true);

  // markSaved 定位到 B：保存期间的 C 手势可撤销，撤销不穿越保存边界
  assert.equal(history.markSaved('B'), true);
  assert.equal(history.isDirty('C'), true, '保存期间的修改相对新基线仍是脏的');
  assert.equal(history.undo('C'), 'B');
  assert.equal(history.isDirty('B'), false);
  assert.equal(history.canUndo, false, '撤销不得穿越保存边界');
});

test('migrateIds rewrites assigned feature ids across all snapshots', () => {
  const featureJson = (featureId) => JSON.stringify({
    type: 'FeatureCollection',
    features: [{ type: 'Feature', properties: { class_code: 1, feature_id: featureId }, geometry: {} }],
  });
  const history = createEditorHistory();
  history.reset(featureJson('auto-142-1'));
  history.record('create', featureJson('client-abc'), 1000); // 提交 B
  history.record('properties', featureJson('client-abc').replace('client-abc', 'client-def'), 1100); // C

  // 服务端把 client-abc 分配为 manual-142-2：画布与历史快照全部同步
  history.migrateIds(new Map([['client-abc', 'manual-142-2']]));
  assert.equal(history.markSaved(featureJson('manual-142-2')), true, '基线与栈内快照同步后必须能定位保存边界');
  assert.equal(history.isDirty(JSON.stringify({
    type: 'FeatureCollection',
    features: [{ type: 'Feature', properties: { class_code: 1, feature_id: 'client-def' }, geometry: {} }],
  })), true, '未提交的 client-def 不受映射影响');
  const restored = history.undo(JSON.stringify({
    type: 'FeatureCollection',
    features: [{ type: 'Feature', properties: { class_code: 1, feature_id: 'client-def' }, geometry: {} }],
  }));
  assert.equal(JSON.parse(restored).features[0].properties.feature_id, 'manual-142-2', '撤销恢复的画布必须是正式 ID');

  // 空/非法映射无操作
  const before = history.isDirty(featureJson('manual-142-2'));
  history.migrateIds(new Map());
  history.migrateIds(null);
  assert.equal(history.isDirty(featureJson('manual-142-2')), before);
});
