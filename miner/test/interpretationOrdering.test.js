import assert from 'node:assert/strict';
import test from 'node:test';

import { sortInterpretationRecords } from '../src/utils/interpretationOrdering.js';

const row = (fid, year, file = '') => ({ data: { fid, year, file } });

test('解译历史排序：年份降序→同年矿山升序→产物名序（原 BFF 塑形行为前移）', () => {
  const sorted = sortInterpretationRecords([
    row(714, 2020), row(713, 2024), row(715, 2020), row(714, 2024), row(713, 2020),
  ]);
  assert.deepEqual(
    sorted.map((r) => `${r.data.fid}:${r.data.year}`),
    ['713:2024', '714:2024', '713:2020', '714:2020', '715:2020'],
  );
});

test('排序不修改原数组且容忍缺字段/空输入', () => {
  const input = [row(2, 2021), row(1, 2022), { data: {} }, null];
  const sorted = sortInterpretationRecords(input);
  assert.deepEqual(input.map((r) => r?.data?.fid ?? null), [2, 1, null, null]); // 原序不动
  assert.equal(sorted.length, 4);
  assert.deepEqual(sortInterpretationRecords([]), []);
  assert.deepEqual(sortInterpretationRecords(null), []);
});
