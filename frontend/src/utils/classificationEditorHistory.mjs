/**
 * 矢量编辑撤销/重做历史（纯逻辑，供 ClassificationResultEditor 使用）。
 *
 * 规则：
 * - 每条撤销记录存的是"手势开始前"的 FeatureCollection JSON；
 * - 顶点拖拽等连续 vertex 手势在 coalesceWindowMs 内合并为一条
 *   （且要求栈非空——首条 vertex 必须入栈，否则首段拖拽不可撤销）；
 * - 栈深上限 limit，超出丢最旧；
 * - baseline 为已保存版本快照，撤销回基线即不再脏。
 */
export function createEditorHistory({ limit = 50, coalesceWindowMs = 500 } = {}) {
  let baselineJson = '';
  let lastJson = '';
  let lastMutateAt = 0;
  let undoStack = [];
  let redoStack = [];

  return {
    reset(baseline) {
      baselineJson = baseline;
      lastJson = baseline;
      lastMutateAt = 0;
      undoStack = [];
      redoStack = [];
    },
    get canUndo() {
      return undoStack.length > 0;
    },
    get canRedo() {
      return redoStack.length > 0;
    },
    isDirty(current) {
      return current !== baselineJson;
    },
    record(kind, json, now) {
      if (json === lastJson) return false;
      const coalesce = kind === 'vertex' && now - lastMutateAt < coalesceWindowMs && undoStack.length > 0;
      if (!coalesce) {
        undoStack.push(lastJson);
        if (undoStack.length > limit) undoStack.shift();
        redoStack = [];
      }
      lastJson = json;
      lastMutateAt = now;
      return !coalesce;
    },
    undo(current) {
      if (!undoStack.length) return null;
      const previous = undoStack.pop();
      redoStack.push(current);
      lastJson = previous;
      lastMutateAt = 0;
      return previous;
    },
    redo(current) {
      if (!redoStack.length) return null;
      const next = redoStack.pop();
      undoStack.push(current);
      lastJson = next;
      lastMutateAt = 0;
      return next;
    },
  };
}
