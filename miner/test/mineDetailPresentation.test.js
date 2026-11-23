import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildClassificationItems,
  buildMatrixYearLabels,
  buildOriginalImageryItems,
  changeMatrixStateNote,
  formatImagerySize,
} from '../src/components/mineDetailPresentation.js';

test('single-year classification stays visible without a change matrix', () => {
  const items = buildClassificationItems({
    old_year: null,
    new_year: 2022,
    images: { old: null, new: '/new.png' },
    has_change_matrix: false,
  });

  assert.deepEqual(items, [
    { key: 'new', title: '当前地物分类', year: 2022, url: '/new.png' },
  ]);
});

test('matrix and classification labels use real result years', () => {
  const data = {
    old_year: 2020,
    new_year: 2022,
    images: { old: '/old.png', new: '/new.png' },
  };

  assert.deepEqual(buildMatrixYearLabels(data), { old: 2020, new: 2022 });
  assert.deepEqual(buildClassificationItems(data).map((item) => item.year), [2020, 2022]);
});

test('buildOriginalImageryItems normalizes rows and flags missing files as non-downloadable', () => {
  const items = buildOriginalImageryItems({
    fid: 713,
    items: [
      {
        job_id: 'j-1', year: 2024, filename: 'abc.tif', size_bytes: 572 * 1024 * 1024,
        file_exists: true, job_status: 'succeeded_with_fallback', created_at: '2026-09-22T08:40:00',
      },
      { job_id: 'j-2', year: null, filename: 'gone.tif', size_bytes: null, file_exists: false, job_status: 'failed' },
    ],
  });
  assert.equal(items.length, 2);
  assert.equal(items[0].title, '原始影像（2024）');
  assert.equal(items[0].sizeText, '572.0MB');
  assert.equal(items[0].statusText, '成功(CPU回退)');
  assert.equal(items[0].downloadable, true);
  // 文件缺失仍保留行（溯源要能看到用过哪个输入），但不可下载
  assert.equal(items[1].title, '原始影像（未知年份）');
  assert.equal(items[1].sizeText, '文件缺失');
  assert.equal(items[1].downloadable, false);
  assert.ok(items[1].key.includes('j-2'));
});

test('buildOriginalImageryItems tolerates missing payload shapes', () => {
  assert.deepEqual(buildOriginalImageryItems(null), []);
  assert.deepEqual(buildOriginalImageryItems({}), []);
  assert.deepEqual(buildOriginalImageryItems({ items: 'not-a-list' }), []);
});

test('formatImagerySize covers the human ladder and malformed input', () => {
  assert.equal(formatImagerySize(0), '0B');
  assert.equal(formatImagerySize(2048), '2.0KB');
  assert.equal(formatImagerySize(3 * 1024 * 1024), '3.0MB');
  assert.equal(formatImagerySize(2.5 * 1024 * 1024 * 1024), '2.50GB');
  assert.equal(formatImagerySize(null), '未知大小');
  assert.equal(formatImagerySize('abc'), '未知大小');
});

test('changeMatrixStateNote stays silent for current matrices and legacy payloads', () => {
  // 已按人工修订重算 / 没有人工修订：不需要提示
  assert.deepEqual(changeMatrixStateNote({ change_matrix_state: 'revised' }, { has_change_matrix: true }), { state: 'revised', note: '' });
  assert.deepEqual(changeMatrixStateNote({ change_matrix_state: 'inference' }, { has_change_matrix: true }), { state: 'inference', note: '' });
  // 旧版后端的响应没有这两个字段：照旧不提示，不报错
  assert.deepEqual(changeMatrixStateNote({}, undefined), { state: null, note: '' });
  assert.deepEqual(changeMatrixStateNote(null, null), { state: null, note: '' });
});

test('changeMatrixStateNote explains terminal states in the backend wording', () => {
  const empty = changeMatrixStateNote(
    { change_matrix_state: 'revised_empty', change_matrix_note: '有一期图斑为空，没有地类转移数据' },
    { has_change_matrix: false },
  );
  assert.equal(empty.note, '有一期图斑为空，没有地类转移数据');

  const unavailable = changeMatrixStateNote(
    { change_matrix_state: 'unavailable', change_matrix_note: '变化矩阵无法按审校矢量计算（2023 年 label 缺失）' },
    { has_change_matrix: true },
  );
  assert.equal(unavailable.note, '变化矩阵无法按审校矢量计算（2023 年 label 缺失）');
});

test('changeMatrixStateNote flags a stale matrix while the rebuild is pending', () => {
  const pending = { change_matrix_state: 'pending', change_matrix_note: '正在按人工修订重算变化矩阵，稍后自动更新' };
  // 页面上还挂着一张（重算前的）矩阵：必须说明它不是现值
  const stale = changeMatrixStateNote(pending, { has_change_matrix: true });
  assert.match(stale.note, /正在按人工修订重算/);
  assert.match(stale.note, /重算前的内容/);
  // 没有矩阵可显示（例如被推理发布清空）：只提示正在重算
  const bare = changeMatrixStateNote(pending, { has_change_matrix: false });
  assert.equal(bare.note, '正在按人工修订重算变化矩阵，稍后自动更新');
  assert.equal(bare.state, 'pending');
});
