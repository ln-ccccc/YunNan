export function buildProjectExportFeatures({ projectDetail, minesData }) {
  const summary = projectDetail?.summary || {};
  const mines = Array.isArray(projectDetail?.mines) ? projectDetail.mines : [];
  const datasets = Array.isArray(projectDetail?.datasets) ? projectDetail.datasets : [];
  const mineFeatureMap = new Map(
    (Array.isArray(minesData) ? minesData : []).map((feature) => [
      Number(feature?.properties?.FID_1),
      feature,
    ]),
  );

  return mines
    .map((mine) => {
      const mineFid = Number(mine.mine_fid);
      const feature = mineFeatureMap.get(mineFid);
      if (!feature?.geometry) return null;
      const dataset = datasets.find((item) => Number(item?.mine_fid) === mineFid) || null;
      return {
        type: 'Feature',
        geometry: feature.geometry,
        properties: {
          project_id: Number(summary.id || 0),
          mine_fid: mineFid,
          mine_name: mine.mine_name_snapshot || feature?.properties?.mine_name || feature?.properties?.name || '',
          dataset_id: dataset?.id ?? null,
          result_type: dataset?.dataset_kind || null,
          year_start: dataset?.year_start ?? null,
          year_end: dataset?.year_end ?? null,
        },
      };
    })
    .filter(Boolean);
}
