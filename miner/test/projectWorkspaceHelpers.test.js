import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildMineSelectionSet,
  filterProjects,
  isSafeIncomingTiffStorageKey,
} from '../src/projectWorkspace/projectWorkspaceHelpers.js';

test('filterProjects matches by name, region, year and status', () => {
  const items = [
    { id: 1, name: '大理一期', region: '大理州', status: 'active', monitor_start_year: 2024, monitor_end_year: 2025 },
    { id: 2, name: '曲靖归档', region: '曲靖市', status: 'archived', monitor_start_year: 2022, monitor_end_year: 2023 },
  ];
  const filtered = filterProjects(items, {
    name: '大理',
    region: '大理',
    status: 'active',
    monitorYear: '2024',
  });

  assert.deepEqual(filtered.map((item) => item.id), [1]);
});

test('buildMineSelectionSet returns bound mine fid values as strings', () => {
  const detail = {
    mines: [
      { mine_fid: 101 },
      { mine_fid: 102 },
    ],
  };

  const selected = buildMineSelectionSet(detail);

  assert.deepEqual(Array.from(selected.values()), ['101', '102']);
});

test('isSafeIncomingTiffStorageKey allows only supported raster files below incoming', () => {
  assert.equal(isSafeIncomingTiffStorageKey('incoming/2024/spring.tif'), true);
  assert.equal(isSafeIncomingTiffStorageKey('incoming\\2024\\spring.TIFF'), true);
  assert.equal(isSafeIncomingTiffStorageKey('incoming/../escape.tif'), false);
  assert.equal(isSafeIncomingTiffStorageKey('incoming//spring.tif'), false);
  assert.equal(isSafeIncomingTiffStorageKey('uploads/spring.tif'), false);
  assert.equal(isSafeIncomingTiffStorageKey('incoming/spring.img'), true);
  assert.equal(isSafeIncomingTiffStorageKey('incoming/spring.jp2'), true);
  assert.equal(isSafeIncomingTiffStorageKey('incoming/spring.png'), false);
});
