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
