<template>
  <div class="workspace-page">
    <header class="workspace-header">
      <div>
        <p class="workspace-kicker">矿山项目化监测平台</p>
        <h1>项目工作台</h1>
        <p class="workspace-subtitle">项目建档、空间资源、成果审阅和配置快照均在受控项目上下文中完成。</p>
      </div>
      <div class="workspace-header-actions">
        <button v-if="username" class="ghost-btn" @click="emit('logout')">{{ username }} 退出登录</button>
        <button class="secondary-btn" :disabled="!overview?.capabilities?.can_open_map" @click="openMap()">
          {{ overview?.capabilities?.can_open_map ? '打开矿山地图' : '地图资源未就绪' }}
        </button>
        <button class="primary-btn" @click="startCreateProject">新建项目</button>
      </div>
    </header>

    <StatsOverviewPanel ref="statsPanelRef" class="workspace-stats" />

    <section class="workspace-layout">
      <ProjectSelector
        :items="projects"
        :selected-id="currentProjectId"
        :filters="filters"
        :loading="listSlice.loading"
        :error="listSlice.error"
        v-model:checked-ids="checkedProjectIds"
        @select="selectProject"
        @update:filters="updateFilters"
        @reset="resetFilters"
        @refresh="loadProjects(currentProjectId)"
        @create="startCreateProject"
        @batch-archive="batchArchiveProjects"
        @batch-delete="batchDeleteProjectsWithConfirm"
      />

      <main class="project-main">
        <ProjectForm
          v-if="showProjectForm"
          :model="projectForm"
          :editing="Boolean(editingProjectId)"
          :busy="savingProject"
          :error="formError"
          @submit="submitProjectForm"
          @cancel="cancelProjectForm"
        />

        <template v-if="currentProjectId">
          <ProjectOverviewPanel
            :overview="overview"
            :loading="slices.overview.loading"
            :busy="projectOperationBusy"
            :error="slices.overview.error"
            @edit="startEditProject"
            @refresh="refreshCurrent(INVALIDATION.project)"
            @archive="archiveProject"
            @restore="restoreProject"
            @delete="deleteProjectWithConfirm"
            @open-map="openMap"
            @start-inference="startInference"
            @run-action="runNextAction"
          />

          <ProjectSpatialResources
            id="spatial-resources"
            :key="`spatial-${currentProjectId}`"
            v-model:open="spatialUi.open"
            v-model:selected-basemap-candidate="spatialUi.selectedBasemapCandidate"
            :spatial="slices.spatial.data"
            :mine-options="mineOptions"
            :can-configure="overview?.capabilities?.can_configure_spatial === true"
            :busy="spatialUi.busy || slices.spatial.loading"
            :error="spatialUi.error || slices.spatial.error"
            :mine-preview="spatialUi.minePreview"
            :basemap-candidates="spatialUi.basemapCandidates"
            @preview-mine="previewMineVector"
            @clear-mine-preview="clearMinePreview"
            @import-mine="importMineVector"
            @load-basemap-candidates="loadBasemapCandidates"
            @register-basemap="registerBasemap"
            @retry-job="retrySpatialJob"
            @cancel-job="cancelSpatialJob"
            @open-map="openMap"
          />

          <ProjectDatasetRegistrationPanel
            id="dataset-registration"
            :key="`dataset-${currentProjectId}-${datasetFormResetKey}`"
            :project-id="currentProjectId"
            :mine-options="mineOptions"
            :busy="savingDataset"
            :error="datasetError"
            @register-dataset="registerDataset"
          />

          <ProjectAssetsPanel
            id="project-assets"
            :items="assetItems"
            :loading="slices.assets.loading"
            :error="slices.assets.error"
            :filters="assetFilters"
            @update:filters="updateAssetFilters"
            @refresh="refreshCurrent(['assets'])"
            @configure-spatial="openSpatialResources"
          />

          <ProjectActivityPanel
            :items="activityItems"
            :loading="slices.activity.loading"
            :error="slices.activity.error"
            @refresh="refreshCurrent(['activity'])"
          />

          <ProjectExportSnapshotPanel
            :exports="exportItems"
            :snapshots="snapshotItems"
            :capabilities="overview?.capabilities || {}"
            :loading="slices.exports.loading || slices.snapshots.loading"
            :busy="projectOperationBusy"
            :error="slices.exports.error || slices.snapshots.error"
            @create-export="createExport"
            @create-snapshot="createSnapshot"
            @restore-snapshot="restoreSnapshot"
          />

          <ProjectArchivePanel
            :project-id="currentProjectId"
            @imported="refreshCurrent(INVALIDATION.project)"
          />

          <ParcelPanel
            :project-id="currentProjectId"
            @locate-mine="locateMine"
          />
        </template>

        <section v-else-if="listSlice.loading" class="panel empty-detail">
          <h2>项目加载中</h2>
          <p>正在读取项目列表。</p>
        </section>
        <section v-else class="panel empty-detail">
          <h2>暂无项目详情</h2>
          <p>先创建项目，或者从左侧选择一个已有项目。</p>
        </section>
      </main>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue';

import StatsOverviewPanel from './StatsOverviewPanel.vue';
import ProjectActivityPanel from './projectWorkspace/ProjectActivityPanel.vue';
import ProjectAssetsPanel from './projectWorkspace/ProjectAssetsPanel.vue';
import ProjectDatasetRegistrationPanel from './projectWorkspace/ProjectDatasetRegistrationPanel.vue';
import ProjectExportSnapshotPanel from './projectWorkspace/ProjectExportSnapshotPanel.vue';
import ProjectArchivePanel from './projectWorkspace/ProjectArchivePanel.vue';
import ParcelPanel from './projectWorkspace/ParcelPanel.vue';
import ProjectForm from './projectWorkspace/ProjectForm.vue';
import ProjectOverviewPanel from './projectWorkspace/ProjectOverviewPanel.vue';
import ProjectSelector from './projectWorkspace/ProjectSelector.vue';
import ProjectSpatialResources from './projectWorkspace/ProjectSpatialResources.vue';
import { createProjectWorkspaceApi } from '../projectWorkspace/projectWorkspaceApi.js';
import { actionTarget, createSelectionGate, createSlice, INVALIDATION } from '../projectWorkspace/projectWorkspaceViewModel.js';

defineProps({ username: { type: String, default: '' } });
const emit = defineEmits(['open-map', 'logout']);
const rawMinerApiBase = import.meta.env.VITE_MINER_API_BASE_URL;
const api = createProjectWorkspaceApi({
  baseUrl: rawMinerApiBase ? String(rawMinerApiBase).replace(/\/$/, '') : '',
});
const selectionGate = createSelectionGate();
const listGate = createSelectionGate();
const minePreviewGate = createSelectionGate();
const spatialMutationGate = createSelectionGate();
const datasetMutationGate = createSelectionGate();
const projectOperationGate = createSelectionGate();
const projectFormGate = createSelectionGate();

const filters = ref({ name: '', region: '', status: '', monitorYear: '' });
const statsPanelRef = ref(null);
const checkedProjectIds = ref([]);
const assetFilters = ref({ type: '', status: '' });
const currentProjectId = ref(null);
const listSlice = reactive(createSlice());
const slices = reactive({
  overview: createSlice(),
  assets: createSlice(),
  spatial: createSlice(),
  activity: createSlice(),
  exports: createSlice(),
  snapshots: createSlice(),
  mineOptions: createSlice(),
});
const spatialUi = reactive({
  open: false,
  busy: false,
  error: '',
  minePreview: null,
  basemapCandidates: [],
  selectedBasemapCandidate: '',
});
const showProjectForm = ref(false);
const editingProjectId = ref(null);
const savingProject = ref(false);
const savingDataset = ref(false);
const datasetFormResetKey = ref(0);
const projectOperationBusy = ref(false);
const formError = ref('');
const datasetError = ref('');
const projectForm = ref(defaultProjectForm());
let spatialPollTimer = null;

const projects = computed(() => listSlice.data?.items || []);
const overview = computed(() => slices.overview.data);
const assetItems = computed(() => slices.assets.data?.items || []);
const activityItems = computed(() => slices.activity.data?.items || []);
const exportItems = computed(() => slices.exports.data?.items || []);
const snapshotItems = computed(() => slices.snapshots.data?.items || []);
const mineOptions = computed(() => (slices.mineOptions.data?.features || []).map((feature) => {
  const properties = feature?.properties || {};
  return {
    fid: String(properties.FID_1),
    name: properties.mine_name || properties.name || `矿山 ${properties.FID_1}`,
    city: properties.SHI || '',
    area: properties.area || properties.TBTYMJ || null,
    status: properties.status_normalized || '',
  };
}));

function defaultProjectForm() {
  return {
    name: '', region: '', manager: '', remark: '', status: 'draft', monitor_start_year: '', monitor_end_year: '',
  };
}

function messageFrom(error, fallback) {
  return error?.response?.data?.msg || error?.message || fallback;
}

function resetSelectedSlices() {
  Object.values(slices).forEach((slice) => Object.assign(slice, createSlice()));
}

function beginProjectRequest({ reset = false } = {}) {
  const revision = selectionGate.next();
  if (reset) {
    resetSelectedSlices();
  } else {
    Object.values(slices).forEach((slice) => {
      slice.loading = false;
    });
  }
  return revision;
}

function resetSpatialUiForProjectChange() {
  minePreviewGate.next();
  spatialMutationGate.next();
  Object.assign(spatialUi, {
    open: false,
    busy: false,
    error: '',
    minePreview: null,
    basemapCandidates: [],
    selectedBasemapCandidate: '',
  });
}

function resetDatasetUiForProjectChange() {
  datasetMutationGate.next();
  savingDataset.value = false;
  datasetError.value = '';
}

function resetProjectOperationForProjectChange() {
  projectOperationGate.next();
  projectOperationBusy.value = false;
}

function startProjectOperation() {
  const projectId = currentProjectId.value;
  if (!projectId || projectOperationBusy.value) return null;
  projectOperationBusy.value = true;
  return { projectId, revision: projectOperationGate.next() };
}

function isCurrentProjectOperation(operation) {
  return Boolean(
    operation
    && currentProjectId.value === operation.projectId
    && projectOperationGate.isCurrent(operation.revision),
  );
}

function startSpatialMutation() {
  const projectId = currentProjectId.value;
  if (!projectId) return null;
  return { projectId, revision: spatialMutationGate.next() };
}

function isCurrentSpatialMutation(mutation) {
  return Boolean(
    mutation
    && currentProjectId.value === mutation.projectId
    && spatialMutationGate.isCurrent(mutation.revision),
  );
}

async function loadProjectList(revision = listGate.next()) {
  listSlice.loading = true;
  listSlice.error = '';
  try {
    const data = await api.loadProjects();
    if (!listGate.isCurrent(revision)) return false;
    listSlice.data = data;
    return true;
  } catch (error) {
    if (listGate.isCurrent(revision)) {
      listSlice.error = messageFrom(error, '项目列表加载失败');
    }
    return false;
  } finally {
    if (listGate.isCurrent(revision)) listSlice.loading = false;
  }
}

async function loadSlice(name, request, revision) {
  if (!selectionGate.isCurrent(revision)) return;
  const slice = slices[name];
  slice.loading = true;
  slice.error = '';
  try {
    const data = await request();
    if (selectionGate.isCurrent(revision)) slice.data = data;
  } catch (error) {
    if (selectionGate.isCurrent(revision)) slice.error = messageFrom(error, '项目数据加载失败');
  } finally {
    if (selectionGate.isCurrent(revision)) slice.loading = false;
  }
}

async function loadSelectedProject(projectId, revision, requestedSlices = Object.keys(slices)) {
  if (!selectionGate.isCurrent(revision) || currentProjectId.value !== projectId) return;
  const loaders = {
    overview: () => api.loadOverview(projectId),
    assets: () => api.loadAssets(projectId, assetFilters.value),
    spatial: () => api.loadSpatial(projectId),
    activity: () => api.loadActivity(projectId),
    exports: () => api.loadExports(projectId),
    snapshots: () => api.loadSnapshots(projectId),
    mineOptions: () => api.loadMineOptions(projectId),
  };
  await Promise.all(requestedSlices
    .filter((name) => Object.hasOwn(loaders, name))
    .map((name) => loadSlice(name, loaders[name], revision)));
  if (selectionGate.isCurrent(revision)) scheduleSpatialPoll();
}

async function loadProjects(preferredProjectId = null) {
  const revision = listGate.next();
  // 看板与项目列表同刷：新建/归档后统计即时跟随
  statsPanelRef.value?.fetchOverview?.();
  if (!await loadProjectList(revision) || listSlice.error) return;
  const items = listSlice.data?.items || [];
  const preferred = preferredProjectId || currentProjectId.value || items[0]?.id;
  if (preferred) await selectProject(preferred);
  else {
    currentProjectId.value = null;
    beginProjectRequest({ reset: true });
    resetSpatialUiForProjectChange();
    resetDatasetUiForProjectChange();
    resetProjectOperationForProjectChange();
  }
}

async function refreshProjectListForForm(preferredProjectId, revision) {
  await loadProjectList();
  if (!projectFormGate.isCurrent(revision)) return false;
  await selectProject(preferredProjectId);
  return currentProjectId.value === Number(preferredProjectId);
}

async function selectProject(projectId) {
  if (!projectId) return;
  const nextProjectId = Number(projectId);
  const changed = currentProjectId.value !== nextProjectId;
  currentProjectId.value = nextProjectId;
  if (changed) {
    listGate.next();
    listSlice.loading = false;
    cancelProjectForm();
    resetSpatialUiForProjectChange();
    resetDatasetUiForProjectChange();
    resetProjectOperationForProjectChange();
  }
  await loadSelectedProject(nextProjectId, beginProjectRequest({ reset: changed }));
}

async function refreshCurrent(requestedSlices) {
  if (!currentProjectId.value) return;
  const names = requestedSlices || Object.keys(slices);
  const projectId = currentProjectId.value;
  const revision = beginProjectRequest();
  if (names.includes('list')) await loadProjectList();
  if (!selectionGate.isCurrent(revision) || currentProjectId.value !== projectId) return;
  await loadSelectedProject(projectId, revision, names.filter((name) => name !== 'list'));
}

function updateFilters(nextFilters) {
  filters.value = { ...filters.value, ...(nextFilters || {}) };
}

function resetFilters() {
  filters.value = { name: '', region: '', status: '', monitorYear: '' };
}

async function updateAssetFilters(nextFilters) {
  assetFilters.value = { ...assetFilters.value, ...(nextFilters || {}) };
  await refreshCurrent(['assets']);
}

function startCreateProject() {
  if (savingProject.value) return;
  editingProjectId.value = null;
  projectForm.value = defaultProjectForm();
  formError.value = '';
  showProjectForm.value = true;
}

function startEditProject() {
  if (savingProject.value) return;
  const summary = overview.value?.summary;
  if (!summary) return;
  editingProjectId.value = overview.value?.project_id || summary.id;
  projectForm.value = {
    name: summary.name || '', region: summary.region || '', manager: summary.manager || '', remark: summary.remark || '',
    status: summary.status || overview.value?.lifecycle_status || 'draft',
    monitor_start_year: summary.monitor_start_year || '', monitor_end_year: summary.monitor_end_year || '',
  };
  formError.value = '';
  showProjectForm.value = true;
}

function cancelProjectForm() {
  projectFormGate.next();
  savingProject.value = false;
  showProjectForm.value = false;
  editingProjectId.value = null;
  formError.value = '';
}

async function submitProjectForm(payload) {
  if (savingProject.value) return;
  const revision = projectFormGate.next();
  const targetProjectId = editingProjectId.value;
  savingProject.value = true;
  formError.value = '';
  try {
    const form = payload || projectForm.value;
    const normalized = { ...form, monitor_start_year: form.monitor_start_year || null, monitor_end_year: form.monitor_end_year || null };
    const created = !targetProjectId;
    const project = targetProjectId
      ? await api.updateProject(targetProjectId, normalized)
      : await api.createProject(normalized);
    if (!projectFormGate.isCurrent(revision)) return;
    showProjectForm.value = false;
    const selected = await refreshProjectListForForm(project?.id || targetProjectId, revision);
    if (created && selected) openSpatialResources();
  } catch (error) {
    if (projectFormGate.isCurrent(revision)) {
      formError.value = messageFrom(error, '项目保存失败');
    }
  } finally {
    if (projectFormGate.isCurrent(revision)) savingProject.value = false;
  }
}

function openSpatialResources() {
  spatialUi.open = true;
}

function runNextAction(action) {
  const actionCode = typeof action === 'string' ? action : action?.action_code;
  const target = actionTarget(actionCode);
  if (!target) return;
  if (target === 'spatial-resources') openSpatialResources();
  const element = typeof document === 'undefined' ? null : document.getElementById(target);
  element?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function openMap(mineFid = null) {
  if (!currentProjectId.value || !overview.value?.capabilities?.can_open_map) return;
  emit('open-map', currentProjectId.value, mineFid === null ? null : Number(mineFid));
}

function locateMine(fid) {
  emit('open-map', currentProjectId.value, fid);
}

function startInference() {
  if (!currentProjectId.value || !overview.value?.capabilities?.can_start_inference) return;
  emit('open-map', currentProjectId.value, null, { openInference: true });
}

async function archiveProject() {
  const operation = startProjectOperation();
  if (!operation) return;
  try {
    await api.archiveProject(operation.projectId);
    if (!isCurrentProjectOperation(operation)) return;
    await refreshCurrent(INVALIDATION.project);
  } catch (error) {
    if (isCurrentProjectOperation(operation)) {
      slices.overview.error = messageFrom(error, '项目归档失败');
    }
  } finally {
    if (projectOperationGate.isCurrent(operation.revision)) projectOperationBusy.value = false;
  }
}

async function batchArchiveProjects(projectIds) {
  if (!Array.isArray(projectIds) || !projectIds.length) return;
  if (!window.confirm(`确认归档选中的 ${projectIds.length} 个项目？`)) return;
  try {
    const result = await api.batchArchiveProjects(projectIds);
    const { succeeded, failed } = result || {};
    window.alert(`批量归档完成：成功 ${succeeded ?? 0} 项，失败 ${failed ?? 0} 项`);
    checkedProjectIds.value = [];
    await loadProjects(currentProjectId.value);
  } catch (error) {
    window.alert(messageFrom(error, '批量归档失败，请稍后重试'));
    // 上游可能 502 而后端已部分成功——强制刷列表避免滞留旧状态
    checkedProjectIds.value = [];
    await loadProjects(currentProjectId.value);
  }
}

async function batchDeleteProjectsWithConfirm(projectIds) {
  if (!Array.isArray(projectIds) || !projectIds.length) return;
  const message = [
    `确认删除选中的 ${projectIds.length} 个项目？`,
    '',
    '· 仅已归档项目会被删除；活动项目将逐项报告"须先归档"',
    '· 存储目录移入回收区（trash），可人工恢复',
  ].join('\n');
  if (!window.confirm(message)) return;
  try {
    const result = await api.batchDeleteProjects(projectIds);
    const lines = (result?.results || [])
      .map((r) => (r.ok ? `✓ ${r.project_id} 已删除` : `✗ ${r.project_id}：${r.msg}`));
    window.alert([`批量删除：成功 ${result?.succeeded ?? 0}，失败 ${result?.failed ?? 0}`, ...lines].join('\n'));
    checkedProjectIds.value = [];
    await loadProjects(null);
  } catch (error) {
    window.alert(messageFrom(error, '批量删除失败，请稍后重试'));
    checkedProjectIds.value = [];
    await loadProjects(null);
  }
}

async function deleteProjectWithConfirm() {
  const summary = overview.value?.summary || {};
  const message = [
    `确认删除项目「${summary.name || currentProjectId.value}」？`,
    '',
    '· 项目将从列表移除（数据库保留审计记录）',
    '· 项目存储目录将移入回收区（trash），可由管理员人工恢复',
    '· 该操作仅对已归档项目可用',
  ].join('\n');
  if (!window.confirm(message)) return;
  const operation = startProjectOperation();
  if (!operation) return;
  try {
    await api.deleteProject(operation.projectId);
    if (!isCurrentProjectOperation(operation)) return;
    // 项目已删：清当前选中，刷新列表与看板
    currentProjectId.value = null;
    beginProjectRequest({ reset: true });
    await loadProjects(null);
  } catch (error) {
    if (isCurrentProjectOperation(operation)) {
      slices.overview.error = messageFrom(error, '项目删除失败');
    }
  } finally {
    if (projectOperationGate.isCurrent(operation.revision)) projectOperationBusy.value = false;
  }
}

async function restoreProject() {
  const operation = startProjectOperation();
  if (!operation) return;
  try {
    await api.restoreProject(operation.projectId);
    if (!isCurrentProjectOperation(operation)) return;
    await refreshCurrent(INVALIDATION.project);
  } catch (error) {
    if (isCurrentProjectOperation(operation)) {
      slices.overview.error = messageFrom(error, '项目恢复失败');
    }
  } finally {
    if (projectOperationGate.isCurrent(operation.revision)) projectOperationBusy.value = false;
  }
}

async function registerDataset(payload) {
  const projectId = currentProjectId.value;
  if (!projectId) return;
  const revision = datasetMutationGate.next();
  savingDataset.value = true;
  datasetError.value = '';
  try {
    await api.registerDataset(projectId, payload);
    if (currentProjectId.value !== projectId || !datasetMutationGate.isCurrent(revision)) return;
    datasetFormResetKey.value += 1;
    await refreshCurrent(INVALIDATION.dataset);
  } catch (error) {
    if (currentProjectId.value === projectId && datasetMutationGate.isCurrent(revision)) {
      datasetError.value = messageFrom(error, '影像登记失败');
    }
  } finally {
    if (datasetMutationGate.isCurrent(revision)) savingDataset.value = false;
  }
}

async function createExport(format) {
  const operation = startProjectOperation();
  if (!operation) return;
  try {
    await api.createExport(operation.projectId, { format });
    if (!isCurrentProjectOperation(operation)) return;
    await refreshCurrent(INVALIDATION.export);
  } catch (error) {
    if (isCurrentProjectOperation(operation)) {
      slices.exports.error = messageFrom(error, '导出创建失败');
    }
  } finally {
    if (projectOperationGate.isCurrent(operation.revision)) projectOperationBusy.value = false;
  }
}

async function createSnapshot() {
  const operation = startProjectOperation();
  if (!operation) return;
  try {
    await api.createSnapshot(operation.projectId);
    if (!isCurrentProjectOperation(operation)) return;
    await refreshCurrent(INVALIDATION.snapshot);
  } catch (error) {
    if (isCurrentProjectOperation(operation)) {
      slices.snapshots.error = messageFrom(error, '项目配置快照创建失败');
    }
  } finally {
    if (projectOperationGate.isCurrent(operation.revision)) projectOperationBusy.value = false;
  }
}

async function restoreSnapshot(snapshotId) {
  if (!snapshotId || !window.confirm(`确认从项目配置快照 ${snapshotId} 恢复项目索引吗？`)) return;
  const operation = startProjectOperation();
  if (!operation) return;
  try {
    await api.restoreSnapshot(operation.projectId, snapshotId);
    if (!isCurrentProjectOperation(operation)) return;
    await refreshCurrent(INVALIDATION.restoreSnapshot);
  } catch (error) {
    if (isCurrentProjectOperation(operation)) {
      slices.snapshots.error = messageFrom(error, '项目配置快照恢复失败');
    }
  } finally {
    if (projectOperationGate.isCurrent(operation.revision)) projectOperationBusy.value = false;
  }
}

async function previewMineVector(payload) {
  const projectId = currentProjectId.value;
  if (!projectId) return;
  const revision = minePreviewGate.next();
  spatialUi.busy = true;
  spatialUi.error = '';
  try {
    const preview = await api.previewMineVector(projectId, payload);
    if (currentProjectId.value === projectId && minePreviewGate.isCurrent(revision)) {
      spatialUi.minePreview = preview;
    }
  } catch (error) {
    if (currentProjectId.value === projectId && minePreviewGate.isCurrent(revision)) {
      spatialUi.error = messageFrom(error, '矿山文件预览失败');
    }
  } finally {
    if (currentProjectId.value === projectId && minePreviewGate.isCurrent(revision)) {
      spatialUi.busy = false;
    }
  }
}

function clearMinePreview() {
  minePreviewGate.next();
  spatialUi.minePreview = null;
  spatialUi.error = '';
}

async function importMineVector(payload) {
  const mutation = startSpatialMutation();
  if (!mutation) return;
  spatialUi.busy = true;
  spatialUi.error = '';
  try {
    await api.importMineVector(mutation.projectId, payload);
    if (!isCurrentSpatialMutation(mutation)) return;
    spatialUi.minePreview = null;
    await refreshCurrent(INVALIDATION.spatial);
    if (!isCurrentSpatialMutation(mutation)) return;
    await loadBasemapCandidates();
  } catch (error) {
    if (isCurrentSpatialMutation(mutation)) {
      spatialUi.error = messageFrom(error, '矿山导入失败');
    }
  } finally {
    if (isCurrentSpatialMutation(mutation)) spatialUi.busy = false;
  }
}

async function loadBasemapCandidates() {
  const mutation = startSpatialMutation();
  if (!mutation) return;
  spatialUi.busy = true;
  spatialUi.error = '';
  try {
    const candidates = await api.listBasemapCandidates(mutation.projectId);
    if (!isCurrentSpatialMutation(mutation)) return;
    spatialUi.basemapCandidates = candidates?.items || [];
    spatialUi.selectedBasemapCandidate = spatialUi.basemapCandidates.find((item) => item.intersects_mines)?.candidate || '';
  } catch (error) {
    if (isCurrentSpatialMutation(mutation)) {
      spatialUi.basemapCandidates = [];
      spatialUi.error = messageFrom(error, '底图候选读取失败');
    }
  } finally {
    if (isCurrentSpatialMutation(mutation)) spatialUi.busy = false;
  }
}

async function registerBasemap(payload) {
  const mutation = startSpatialMutation();
  if (!mutation) return;
  spatialUi.busy = true;
  spatialUi.error = '';
  try {
    await api.registerBasemap(mutation.projectId, payload);
    if (!isCurrentSpatialMutation(mutation)) return;
    await refreshCurrent(INVALIDATION.spatial);
  } catch (error) {
    if (isCurrentSpatialMutation(mutation)) {
      spatialUi.error = messageFrom(error, '底图处理启动失败');
    }
  } finally {
    if (isCurrentSpatialMutation(mutation)) spatialUi.busy = false;
  }
}

async function retrySpatialJob(jobId) {
  const mutation = startSpatialMutation();
  if (!mutation) return;
  spatialUi.busy = true;
  try {
    await api.retrySpatialJob(mutation.projectId, jobId);
    if (!isCurrentSpatialMutation(mutation)) return;
    await refreshCurrent(INVALIDATION.spatial);
  } catch (error) {
    if (isCurrentSpatialMutation(mutation)) {
      spatialUi.error = messageFrom(error, '空间任务重试失败');
    }
  } finally {
    if (isCurrentSpatialMutation(mutation)) spatialUi.busy = false;
  }
}

async function cancelSpatialJob(jobId) {
  const mutation = startSpatialMutation();
  if (!mutation) return;
  spatialUi.busy = true;
  try {
    await api.cancelSpatialJob(mutation.projectId, jobId);
    if (!isCurrentSpatialMutation(mutation)) return;
    await refreshCurrent(INVALIDATION.spatial);
  } catch (error) {
    if (isCurrentSpatialMutation(mutation)) {
      spatialUi.error = messageFrom(error, '空间任务取消失败');
    }
  } finally {
    if (isCurrentSpatialMutation(mutation)) spatialUi.busy = false;
  }
}

function scheduleSpatialPoll() {
  if (spatialPollTimer) window.clearTimeout(spatialPollTimer);
  const activeJob = (slices.spatial.data?.jobs || []).find((job) => ['queued', 'running'].includes(job.status));
  const projectId = currentProjectId.value;
  if (!projectId || !activeJob) return;
  spatialPollTimer = window.setTimeout(async () => {
    if (currentProjectId.value === projectId) await refreshCurrent(INVALIDATION.spatial);
  }, 2000);
}

onMounted(() => loadProjects());
onUnmounted(() => {
  if (spatialPollTimer) window.clearTimeout(spatialPollTimer);
});
</script>

<style scoped>
.workspace-page { min-height: 100vh; box-sizing: border-box; padding: 24px; color: #163030; background: radial-gradient(circle at top left, rgba(18, 125, 110, 0.18), transparent 30%), #f7faf8; }
.workspace-header, .workspace-header-actions, .workspace-layout { display: flex; gap: 16px; }
.workspace-header { justify-content: space-between; align-items: flex-start; margin-bottom: 20px; }
.workspace-header-actions { align-items: center; }
.workspace-kicker { margin: 0 0 8px; color: #2f7a68; font-size: 13px; letter-spacing: 0.08em; }
.workspace-header h1, .workspace-subtitle { margin: 0; }
.workspace-subtitle { margin-top: 8px; color: #5d6f6d; }
.workspace-layout { align-items: flex-start; }
.project-main { min-width: 0; flex: 1; display: flex; flex-direction: column; gap: 16px; }
.panel { box-sizing: border-box; padding: 18px; border: 1px solid rgba(35, 86, 78, 0.1); border-radius: 18px; background: rgba(255, 255, 255, 0.88); box-shadow: 0 12px 30px rgba(31, 66, 61, 0.08); }
.empty-detail { min-height: 300px; display: flex; flex-direction: column; justify-content: center; }
button { padding: 10px 14px; border-radius: 12px; font: inherit; cursor: pointer; }
.primary-btn { border: 0; color: #fff; background: #2f7a68; }
.secondary-btn { border: 1px solid rgba(47, 122, 104, 0.2); color: #1f5c4d; background: #eaf6f1; }
.ghost-btn { border: 0; color: #2f7a68; background: transparent; }
button:disabled { cursor: not-allowed; opacity: 0.55; }
@media (max-width: 1100px) { .workspace-header, .workspace-layout { flex-direction: column; } .workspace-header-actions { flex-wrap: wrap; } }
</style>
