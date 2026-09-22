import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildClassificationItems,
  buildMatrixYearLabels,
  buildOriginalImageryItems,
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
