import assert from 'node:assert/strict';
import test from 'node:test';

import { buildKmlRoiArgs } from '../services/kmlInferenceArgs.js';

test('buildKmlRoiArgs includes single year and omits old/new years', () => {
  const args = buildKmlRoiArgs({
    scriptPath: '/app/backend/kml_roi_infer.py',
    oldTifPath: '/data/old.tif',
    newTifPath: '/data/new.tif',
    kmlPath: '/data/mines.kml',
    outputRoot: '/data/out',
    device: 'cpu',
    limit: 0,
    year: '2025',
    oldYear: '2023',
    newYear: '2024',
  });

  assert.deepEqual(args.slice(-2), ['--year', '2025']);
  assert.equal(args.includes('--old_year'), false);
  assert.equal(args.includes('--new_year'), false);
});

test('buildKmlRoiArgs includes old/new years when single year is not provided', () => {
  const args = buildKmlRoiArgs({
    scriptPath: '/app/backend/kml_roi_infer.py',
    oldTifPath: '/data/old.tif',
    newTifPath: '/data/new.tif',
    kmlPath: '/data/mines.kml',
    outputRoot: '/data/out',
    device: 'cuda:0',
    limit: 12,
    oldYear: '2020',
    newYear: '2025',
  });

  assert.deepEqual(args.slice(-6), ['--limit', '12', '--old_year', '2020', '--new_year', '2025']);
});
