import fs from 'fs';
import path from 'path';

export const CLASS_OPTIONS = [
  { key: 'grassland', label: '草地' },
  { key: 'forest', label: '林地' },
  { key: 'building', label: '建筑' },
  { key: 'road', label: '道路' },
  { key: 'bareground', label: '裸土' },
  { key: 'water', label: '水体' },
];

const VALID_DIRECTIONS = new Set(['upward', 'downward', 'stable', 'all']);

const toFinite = (v) => {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

const round = (v, digits = 6) => {
  const n = Number(v);
  return Number.isFinite(n) ? Number(n.toFixed(digits)) : null;
};

const getMineAreaM2 = (properties = {}) => {
  return toFinite(properties.area)
    ?? toFinite(properties.TBTYMJ)
    ?? toFinite(properties.TBTYMJ_1)
    ?? toFinite(properties.SHAPE_Area);
};

const readClassRatio = (outputRoot, fid) => {
  const ratioPath = path.resolve(outputRoot, String(fid), 'class_ratio_percent.json');
  if (!fs.existsSync(ratioPath)) return null;
  try {
    return JSON.parse(fs.readFileSync(ratioPath, 'utf-8'));
  } catch (_) {
    return null;
  }
};

const buildRow = ({ feature, ratio, className }) => {
  const years = Array.isArray(ratio?.years) ? ratio.years.map(Number).filter(Number.isFinite) : [];
  const series = Array.isArray(ratio?.series_percent?.[className])
    ? ratio.series_percent[className].map(Number)
    : [];
  if (years.length < 2 || series.length < 2) return null;

  const startYear = years[0];
  const endYear = years[years.length - 1];
  const startPercent = toFinite(series[0]);
  const endPercent = toFinite(series[series.length - 1]);
  if (startPercent === null || endPercent === null) return null;

  const p = feature?.properties || {};
  const fid = toFinite(p.FID_1 ?? ratio?.fid);
  const mineAreaM2 = getMineAreaM2(p);
  const mineAreaKm2 = mineAreaM2 === null ? null : mineAreaM2 / 1e6;
  const startAreaKm2 = mineAreaKm2 === null ? null : mineAreaKm2 * startPercent / 100;
  const endAreaKm2 = mineAreaKm2 === null ? null : mineAreaKm2 * endPercent / 100;

  return {
    fid: fid === null ? p.FID_1 : fid,
    mine_name: p.mine_name || p.name || `Mine_${p.FID_1 ?? ratio?.fid ?? ''}`,
    start_year: startYear,
    end_year: endYear,
    start_percent: round(startPercent),
    end_percent: round(endPercent),
    delta_percent: round(endPercent - startPercent),
    mine_area_m2: mineAreaM2,
    mine_area_km2: round(mineAreaKm2),
    start_area_km2: round(startAreaKm2),
    end_area_km2: round(endAreaKm2),
    delta_area_km2: round(endAreaKm2 === null || startAreaKm2 === null ? null : endAreaKm2 - startAreaKm2),
  };
};

const matchesDirection = (row, direction) => {
  if (direction === 'all') return true;
  if (direction === 'upward') return row.delta_percent > 0;
  if (direction === 'downward') return row.delta_percent < 0;
  return row.delta_percent === 0;
};

export function buildTrendReport({ outputRoot, minesData, className = 'bareground', direction = 'all' }) {
  const selectedClass = CLASS_OPTIONS.some((item) => item.key === className) ? className : 'bareground';
  const selectedDirection = VALID_DIRECTIONS.has(direction) ? direction : 'all';
  const rows = [];
  let missingCount = 0;

  (minesData || []).forEach((feature) => {
    const fid = feature?.properties?.FID_1;
    const ratio = readClassRatio(outputRoot, fid);
    if (!ratio) {
      missingCount += 1;
      return;
    }
    const row = buildRow({ feature, ratio, className: selectedClass });
    if (row) rows.push(row);
    else missingCount += 1;
  });

  const selectedRows = rows.filter((row) => matchesDirection(row, selectedDirection));

  return {
    mine_total: (minesData || []).length,
    coverage: {
      matrix_ready_count: rows.length,
      matrix_missing_count: missingCount,
    },
    available_classes: CLASS_OPTIONS,
    filters: {
      class_name: selectedClass,
      direction: selectedDirection,
    },
    class_trends: {
      selected_class: {
        upward_count: rows.filter((row) => row.delta_percent > 0).length,
        downward_count: rows.filter((row) => row.delta_percent < 0).length,
        stable_count: rows.filter((row) => row.delta_percent === 0).length,
      },
    },
    tables: {
      selected_class_rows: selectedRows,
    },
  };
}
