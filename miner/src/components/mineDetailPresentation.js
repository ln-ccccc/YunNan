export function buildClassificationItems(changeMatrixData) {
  const images = changeMatrixData?.images || {};
  const items = [];
  if (images.old) {
    items.push({
      key: 'old',
      title: '前期地物分类',
      year: changeMatrixData?.old_year ?? null,
      url: images.old,
    });
  }
  if (images.new) {
    items.push({
      key: 'new',
      title: '当前地物分类',
      year: changeMatrixData?.new_year ?? null,
      url: images.new,
    });
  }
  return items;
}

export function buildMatrixYearLabels(changeMatrixData) {
  return {
    old: changeMatrixData?.old_year ?? '前期',
    new: changeMatrixData?.new_year ?? '当前',
  };
}


const JOB_STATUS_TEXT = {
  succeeded: '成功',
  succeeded_with_fallback: '成功(CPU回退)',
  partial_failed: '部分失败',
  failed: '失败',
  cancelled: '已取消',
  running: '运行中',
  queued: '排队中',
};

export function formatImagerySize(bytes) {
  // Number(null) 是 0 而非 NaN：null/undefined/空串先挡掉再数值化
  if (bytes === null || bytes === undefined || bytes === '') return '未知大小';
  const size = Number(bytes);
  if (!Number.isFinite(size) || size < 0) return '未知大小';
  if (size >= 1024 * 1024 * 1024) return `${(size / (1024 * 1024 * 1024)).toFixed(2)}GB`;
  if (size >= 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)}MB`;
  if (size >= 1024) return `${(size / 1024).toFixed(1)}KB`;
  return `${size}B`;
}

export function formatImageryTime(iso) {
  if (!iso) return '时间未知';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '时间未知';
  return date.toLocaleString('zh-CN', { hour12: false });
}

/**
 * 原始影像溯源列表行（MineDetailModal 原始影像标签页）：
 * 文件缺失的行保留但标记不可下载（溯源要能看到"曾用过哪个输入"）。
 */
export function buildOriginalImageryItems(originalImageryData) {
  const rawItems = originalImageryData?.items;
  if (!Array.isArray(rawItems)) return [];
  return rawItems.map((item) => ({
    key: `${item.job_id}-${item.year ?? 'na'}`,
    jobId: item.job_id,
    title: item.year ? `原始影像（${item.year}）` : '原始影像（未知年份）',
    filename: item.filename || '未知文件名',
    sizeText: item.file_exists ? formatImagerySize(item.size_bytes) : '文件缺失',
    timeText: formatImageryTime(item.created_at),
    statusText: JOB_STATUS_TEXT[item.job_status] || item.job_status || '未知状态',
    downloadable: Boolean(item.file_exists && item.job_id),
    year: item.year ?? null,
  }));
}

/**
 * 溯源面板构建（M3）：traceability 聚合 → 渲染行。
 * 历年成果行（图 URL + 占比 + 图斑数）按年倒序；占比序列与修订行随年份挂接。
 */
export function buildTraceabilityYears(traceability) {
  const years = traceability?.years;
  if (!Array.isArray(years)) return [];
  return [...years]
    .sort((a, b) => (b.year ?? 0) - (a.year ?? 0))
    .map((entry) => ({
      key: `${entry.year}-${entry.result_id}`,
      year: entry.year ?? null,
      resultId: entry.result_id ?? null,
      vectorStatus: entry.vector_status ?? null,
      featureCount: entry.feature_count ?? 0,
      resultImage: entry.result_image_url || null,
      sourceImage: entry.source_image_url || null,
      classRatio: entry.class_ratio_percent || null,
      topClass: entry.class_ratio_percent
        ? Object.entries(entry.class_ratio_percent).sort((a, b) => b[1] - a[1])[0]?.[0] || null
        : null,
      revisionCount: Array.isArray(entry.revisions) ? entry.revisions.length : 0,
      revisions: (entry.revisions || []).map((r) => ({
        key: `${entry.year}-${r.revision_no}-${r.created_at || ''}`,
        label: `v${r.revision_no}`,
        action: r.action || '',
        actor: r.actor || '',
        time: r.created_at || '',
      })),
    }));
}

const RATIO_CLASS_LABELS = {
  grassland: '草地', forest: '林地', building: '建筑', road: '道路', bareground: '裸地', water: '水体',
};

export function formatRatioClass(name) {
  return RATIO_CLASS_LABELS[name] || name || '';
}

/** 占比序列 → 堆叠时序数据行（每类一行，按年取值），供简单条形/表格渲染 */
export function buildRatioSeriesRows(traceability) {
  const series = traceability?.ratio_series;
  const years = series?.years;
  const percent = series?.series_percent;
  if (!Array.isArray(years) || !years.length || !percent || typeof percent !== 'object') return [];
  return Object.entries(percent)
    .filter(([, values]) => Array.isArray(values))
    .map(([name, values]) => ({
      name,
      label: RATIO_CLASS_LABELS[name] || name,
      values: years.map((_, index) => Number(values[index] ?? 0)),
    }));
}
