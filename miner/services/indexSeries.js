export const INDEX_SOURCE_FILES = {
  ndvi: 'NDVI_2year.xlsx',
  ndbi: 'NDBI_by_fid_2year_avg.xlsx',
  ndwi: 'NDWI_by_fid_2year_avg.xlsx',
  ndsi: 'NDSI_by_fid_2year_avg.xlsx',
};

export function calculateStats(data) {
  if (!data || data.length === 0) {
    return { mean: 0, trend: 0, mk_trend: 'no_data' };
  }

  const values = data.map((item) => Number(item.value)).filter(Number.isFinite);
  const years = data.map((item) => Number(item.year)).filter(Number.isFinite);
  if (!values.length || !years.length || values.length !== years.length) {
    return { mean: 0, trend: 0, mk_trend: 'no_data' };
  }

  const mean = values.reduce((sum, value) => sum + value, 0) / values.length;
  const n = values.length;
  if (n < 2) {
    return { mean: Number(mean.toFixed(3)), trend: 0, mk_trend: 'insufficient_data' };
  }

  const sumX = years.reduce((sum, year) => sum + year, 0);
  const sumY = values.reduce((sum, value) => sum + value, 0);
  const sumXY = years.reduce((sum, year, index) => sum + year * values[index], 0);
  const sumXX = years.reduce((sum, year) => sum + year * year, 0);
  const denom = n * sumXX - sumX * sumX;
  const slope = denom !== 0 ? (n * sumXY - sumX * sumY) / denom : 0;
  const mkTrend = slope > 0.0005 ? 'upward' : (slope < -0.0005 ? 'downward' : 'stable');

  return {
    mean: Number(mean.toFixed(3)),
    trend: Number(slope.toFixed(5)),
    mk_trend: mkTrend,
  };
}

function buildUnavailableMessage(indexKey, reason, sourceFile, fid) {
  const upperKey = String(indexKey || '').toUpperCase();
  if (reason === 'load_failed') {
    return `${upperKey} 指数文件加载失败：${sourceFile}`;
  }
  if (reason === 'missing_mine_data') {
    return `${upperKey} 当前矿山无历史数据（FID ${fid}）`;
  }
  return `${upperKey} 指数源文件缺失：${sourceFile}`;
}

export function buildIndicesPayload({ fid, sourceData = {}, availability = {} }) {
  const payload = { fid: Number(fid) };
  for (const key of Object.keys(INDEX_SOURCE_FILES)) {
    const rawData = Array.isArray(sourceData[key]) ? sourceData[key] : [];
    const status = availability[key] || {};
    const sourceAvailable = status.available !== false;
    const hasMineData = rawData.length > 0;
    const available = sourceAvailable && hasMineData;
    const sourceFile = status.source_file || INDEX_SOURCE_FILES[key];
    const reason = !sourceAvailable
      ? (status.reason || 'missing_source_file')
      : (hasMineData ? null : 'missing_mine_data');

    payload[key] = {
      data: rawData,
      ...calculateStats(rawData),
      available,
      source_file: sourceFile,
      reason,
      message: available ? '' : buildUnavailableMessage(key, reason, sourceFile, Number(fid)),
    };
  }
  return payload;
}
