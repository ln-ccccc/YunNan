import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import { buildTrendReport } from '../services/trendReport.js';

test('buildTrendReport returns real class deltas from class_ratio_percent.json', () => {
  const outputRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'trend-report-'));
  const fidDir = path.join(outputRoot, '101');
  fs.mkdirSync(fidDir, { recursive: true });
  fs.writeFileSync(
    path.join(fidDir, 'class_ratio_percent.json'),
    JSON.stringify({
      fid: '101',
      class_names: ['grassland', 'forest', 'building', 'road', 'bareground', 'water'],
      years: [2024, 2025],
      series_percent: {
        forest: [12.5, 18.75],
        grassland: [20, 10],
      },
    }),
    'utf-8',
  );

  const report = buildTrendReport({
    outputRoot,
    minesData: [{
      properties: {
        FID_1: 101,
        mine_name: '测试矿山',
        area: 2000000,
      },
    }],
    className: 'forest',
    direction: 'upward',
  });

  assert.equal(report.mine_total, 1);
  assert.equal(report.coverage.matrix_ready_count, 1);
  assert.equal(report.class_trends.selected_class.upward_count, 1);
  assert.equal(report.class_trends.selected_class.downward_count, 0);
  assert.equal(report.tables.selected_class_rows.length, 1);
  assert.deepEqual(report.tables.selected_class_rows[0], {
    fid: 101,
    mine_name: '测试矿山',
    start_year: 2024,
    end_year: 2025,
    start_percent: 12.5,
    end_percent: 18.75,
    delta_percent: 6.25,
    mine_area_m2: 2000000,
    mine_area_km2: 2,
    start_area_km2: 0.25,
    end_area_km2: 0.375,
    delta_area_km2: 0.125,
  });
});

test('buildTrendReport filters downward rows without inventing stable zero rows', () => {
  const outputRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'trend-report-'));
  const fidDir = path.join(outputRoot, '102');
  fs.mkdirSync(fidDir, { recursive: true });
  fs.writeFileSync(
    path.join(fidDir, 'class_ratio_percent.json'),
    JSON.stringify({
      years: [2022, 2025],
      series_percent: {
        water: [9, 4],
      },
    }),
    'utf-8',
  );

  const report = buildTrendReport({
    outputRoot,
    minesData: [{ properties: { FID_1: 102, name: '水体矿山' } }],
    className: 'water',
    direction: 'downward',
  });

  assert.equal(report.tables.selected_class_rows.length, 1);
  assert.equal(report.tables.selected_class_rows[0].delta_percent, -5);
  assert.equal(report.class_trends.selected_class.stable_count, 0);
});
