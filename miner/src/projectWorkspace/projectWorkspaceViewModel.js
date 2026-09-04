export const ACTIVITY_LABELS = {
  PROJECT_CREATED: '创建项目',
  PROJECT_UPDATED: '更新项目',
  MINE_BINDING_REPLACED: '更新矿山绑定',
  DATASET_REGISTERED: '登记推理影像',
  PROJECT_ARCHIVED: '归档项目',
  PROJECT_RESTORED: '恢复项目',
  SPATIAL_RESOURCE_REMOVED: '移除空间资源版本',
  SPATIAL_RESOURCE_ACTIVATED: '激活空间资源',
  SPATIAL_JOB_QUEUED: '提交空间处理任务',
  SPATIAL_JOB_RETRIED: '重试空间处理任务',
  SPATIAL_JOB_CANCEL_REQUESTED: '请求取消空间处理任务',
  EXPORT_CREATED: '生成导出成果',
  SNAPSHOT_CREATED: '生成项目配置快照',
  SNAPSHOT_RESTORED: '恢复项目配置快照',
};

export function formatActivityAction(actionCode) {
  return ACTIVITY_LABELS[actionCode] || actionCode || '未知操作';
}


export function createSlice() {
  return { data: null, loading: false, error: '' };
}


export function createSelectionGate() {
  let revision = 0;
  return {
    next() {
      revision += 1;
      return revision;
    },
    isCurrent(token) {
      return token === revision;
    },
  };
}


export const INVALIDATION = {
  project: ['list', 'overview', 'activity'],
  spatial: ['overview', 'assets', 'activity', 'mineOptions', 'spatial'],
  dataset: ['overview', 'assets', 'activity'],
  export: ['overview', 'assets', 'exports', 'activity'],
  snapshot: ['overview', 'assets', 'snapshots', 'activity'],
  restoreSnapshot: ['list', 'overview', 'assets', 'activity', 'exports', 'snapshots', 'mineOptions', 'spatial'],
};


export const NEXT_ACTION_LABELS = {
  IMPORT_MINE_BOUNDARY: '导入矿山边界',
  CONFIGURE_BASEMAP: '配置空间资源',
  REGISTER_INFERENCE_INPUT: '登记推理影像',
  REVIEW_RESULT: '审阅成果',
};

export const NEXT_ACTION_TARGETS = {
  IMPORT_MINE_BOUNDARY: 'spatial-resources',
  CONFIGURE_BASEMAP: 'spatial-resources',
  REGISTER_INFERENCE_INPUT: 'dataset-registration',
  REVIEW_RESULT: 'project-assets',
};


export function formatActionLabel(actionCode) {
  return NEXT_ACTION_LABELS[actionCode] || actionCode || '未知操作';
}


export function actionTarget(actionCode) {
  return NEXT_ACTION_TARGETS[actionCode] || null;
}


export function toAssetRow(asset = {}) {
  return {
    id: asset.id || '',
    name: asset.name || '未命名资产',
    assetType: asset.asset_type || '',
    format: asset.format || '',
    status: asset.status || 'registered',
    version: asset.version ?? null,
    error: asset.error || null,
  };
}


export function resolveSpatialWizardStep(spatial = {}) {
  const jobs = Array.isArray(spatial?.jobs) ? spatial.jobs : [];
  if (jobs.some((job) => ['queued', 'running'].includes(job?.status))) return 4;

  const missingResources = spatial?.missing_resources;
  if (!Array.isArray(missingResources) || missingResources.includes('mine_vector')) return 2;
  if (missingResources.includes('basemap')) return 3;
  return 4;
}
