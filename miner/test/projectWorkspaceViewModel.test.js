import assert from 'node:assert/strict';
import test from 'node:test';

import * as projectWorkspaceViewModel from '../src/projectWorkspace/projectWorkspaceViewModel.js';

const {
  ACTIVITY_LABELS,
  actionTarget,
  createSelectionGate,
  createSlice,
  formatActivityAction,
  formatActionLabel,
  INVALIDATION,
  resolveSpatialWizardStep,
  toAssetRow,
} = projectWorkspaceViewModel;

test('formatActivityAction maps audit action codes without inferring business state', () => {
  assert.deepEqual(ACTIVITY_LABELS, {
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
  });
  assert.equal(formatActivityAction('PROJECT_CREATED'), '创建项目');
  assert.equal(formatActivityAction('SNAPSHOT_RESTORED'), '恢复项目配置快照');
  assert.equal(formatActivityAction('DATASET_REGISTERED'), '登记推理影像');
  assert.equal(formatActivityAction(), '未知操作');
});

test('selection gate rejects stale project responses', () => {
  const gate = createSelectionGate();
  const projectA = gate.next();
  const projectB = gate.next();

  assert.equal(gate.isCurrent(projectA), false);
  assert.equal(gate.isCurrent(projectB), true);
  assert.deepEqual(createSlice(), { data: null, loading: false, error: '' });
});

test('view model exposes fixed invalidation and public asset presentation', () => {
  assert.deepEqual(INVALIDATION.dataset, ['overview', 'assets', 'activity']);
  assert.deepEqual(INVALIDATION.export, ['overview', 'assets', 'exports', 'activity']);
  assert.deepEqual(INVALIDATION.snapshot, ['overview', 'assets', 'snapshots', 'activity']);
  assert.equal(formatActionLabel('CONFIGURE_BASEMAP'), '配置空间资源');
  assert.equal(formatActionLabel('UNKNOWN_ACTION'), 'UNKNOWN_ACTION');
  assert.deepEqual(
    toAssetRow({ id: 'dataset:1', name: '2024影像', status: 'ready', file_path: 'D:/secret.tif' }),
    {
      id: 'dataset:1',
      name: '2024影像',
      assetType: '',
      format: '',
      status: 'ready',
      version: null,
      error: null,
    },
  );
});

test('next actions map only to workspace navigation targets', () => {
  assert.equal(actionTarget('IMPORT_MINE_BOUNDARY'), 'spatial-resources');
  assert.equal(actionTarget('REGISTER_INFERENCE_INPUT'), 'dataset-registration');
  assert.equal(actionTarget('REVIEW_RESULT'), 'project-assets');
  assert.equal(actionTarget('UNKNOWN_ACTION'), null);
});

test('resolveSpatialWizardStep only locks the wizard for queued or running jobs', () => {
  assert.equal(typeof resolveSpatialWizardStep, 'function');
  assert.equal(resolveSpatialWizardStep({ jobs: [{ status: 'queued' }], missing_resources: ['basemap'] }), 4);
  assert.equal(resolveSpatialWizardStep({ jobs: [{ status: 'running' }], missing_resources: ['basemap'] }), 4);
  assert.equal(resolveSpatialWizardStep({ jobs: [{ status: 'failed' }], missing_resources: ['basemap'] }), 3);
  assert.equal(resolveSpatialWizardStep({ jobs: [{ status: 'cancelled' }], missing_resources: ['basemap'] }), 3);
  assert.equal(resolveSpatialWizardStep({ missing_resources: ['mine_vector', 'basemap'] }), 2);
  assert.equal(resolveSpatialWizardStep({ missing_resources: [] }), 4);
  assert.equal(resolveSpatialWizardStep({}), 4);
  assert.equal(resolveSpatialWizardStep(null), 4);
});
