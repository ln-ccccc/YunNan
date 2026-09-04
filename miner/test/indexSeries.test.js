import assert from 'node:assert/strict';
import test from 'node:test';

import { buildIndicesPayload, calculateStats } from '../services/indexSeries.js';

test('calculateStats returns no_data when no series exists', () => {
  assert.deepEqual(calculateStats([]), {
    mean: 0,
    trend: 0,
    mk_trend: 'no_data',
  });
});

test('buildIndicesPayload marks missing source files as unavailable', () => {
  const payload = buildIndicesPayload({
    fid: 696,
    sourceData: {
      ndvi: [{ year: 2024, value: 0.31 }],
      ndbi: [],
      ndwi: [{ year: 2024, value: 0.11 }],
      ndsi: [],
    },
    availability: {
      ndvi: { available: true, source_file: 'NDVI_2year.xlsx', reason: null },
      ndbi: { available: false, source_file: 'NDBI_by_fid_2year_avg.xlsx', reason: 'missing_source_file' },
      ndwi: { available: true, source_file: 'NDWI_by_fid_2year_avg.xlsx', reason: null },
      ndsi: { available: false, source_file: 'NDSI_by_fid_2year_avg.xlsx', reason: 'missing_source_file' },
    },
  });

  assert.equal(payload.fid, 696);
  assert.equal(payload.ndvi.available, true);
  assert.equal(payload.ndvi.reason, null);
  assert.equal(payload.ndbi.available, false);
  assert.equal(payload.ndbi.reason, 'missing_source_file');
  assert.match(payload.ndbi.message, /NDBI/);
  assert.match(payload.ndsi.message, /NDSI/);
});

test('buildIndicesPayload marks missing mine history as unavailable for the current fid', () => {
  const payload = buildIndicesPayload({
    fid: 11191,
    sourceData: {
      ndvi: [{ year: 2024, value: 0.31 }],
      ndbi: [],
      ndwi: [{ year: 2024, value: 0.11 }],
      ndsi: [],
    },
    availability: {
      ndvi: { available: true, source_file: 'NDVI_2year.xlsx', reason: null },
      ndbi: { available: true, source_file: 'NDBI_by_fid_2year_avg.xlsx', reason: null },
      ndwi: { available: true, source_file: 'NDWI_by_fid_2year_avg.xlsx', reason: null },
      ndsi: { available: true, source_file: 'NDSI_by_fid_2year_avg.xlsx', reason: null },
    },
  });

  assert.equal(payload.ndbi.available, false);
  assert.equal(payload.ndbi.reason, 'missing_mine_data');
  assert.match(payload.ndbi.message, /11191/);
  assert.equal(payload.ndsi.available, false);
  assert.equal(payload.ndsi.reason, 'missing_mine_data');
  assert.match(payload.ndsi.message, /11191/);
});
