import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildClassificationItems,
  buildMatrixYearLabels,
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
