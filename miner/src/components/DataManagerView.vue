<template>
  <div class="data-manager-view">
    <header class="view-header">
      <div>
        <p class="kicker">数据管理</p>
        <h1>数据导出与配置快照</h1>
        <p class="muted-text">
          按项目导出成果（GeoJSON/CSV/SHP/XLSX）与生成/恢复项目配置快照；
          快照跨环境导入在项目工作台的档案面板完成。
        </p>
      </div>
    </header>

    <section class="view-body">
      <p class="muted-text import-hint">
        快照 manifest 可在「项目管理 → 项目档案 → 配置快照 → 导入快照」用于跨环境恢复。
      </p>
      <div class="toolbar-row">
        <label for="data-manager-project">项目</label>
        <select id="data-manager-project" v-model="selectedProjectId" @change="resetAndLoad">
          <option :value="null" disabled>请选择项目</option>
          <option v-for="project in projects" :key="project.id" :value="project.id">
            {{ project.name }}（{{ formatLifecycleStatus(project.status) }}）
          </option>
        </select>
      </div>

      <p v-if="!selectedProjectId" class="empty-block">先选择项目</p>
      <ProjectExportSnapshotPanel
        v-else
        :exports="exports"
        :snapshots="snapshots"
        :capabilities="capabilities"
        :loading="loading"
        :busy="busy"
        :error="pageError || operationError"
        @create-export="createExport"
        @create-snapshot="createSnapshot"
        @restore-snapshot="restoreSnapshot"
        @download-export="downloadExport"
        @download-snapshot="downloadSnapshot"
      />
    </section>
  </div>
</template>

<script setup>
import axios from 'axios';
import { ref } from 'vue';

import ProjectExportSnapshotPanel from './projectWorkspace/ProjectExportSnapshotPanel.vue';
import { getSelectedProjectId, setSelectedProjectId } from '../services/projectSelectionStore.js';
import { formatLifecycleStatus } from '../utils/projectStatusLabels.js';

const MINER_API_BASE_URL = import.meta.env.VITE_MINER_API_BASE_URL || '';
const apiBase = MINER_API_BASE_URL ? String(MINER_API_BASE_URL).replace(/\/$/, '') : '';
const url = (path) => `${apiBase}${path}`;
const unwrap = async (request) => {
  const response = await request;
  if (response?.data?.success === false) {
    throw new Error(response.data.msg || '请求失败');
  }
  return response?.data?.data ?? response?.data;
};

const projects = ref([]);
const selectedProjectId = ref(null);
const exports = ref([]);
const snapshots = ref([]);
const capabilities = ref({});
const loading = ref(false);
const busy = ref(false);
const downloading = ref(false);

// 错误提示本地化：只直出后端 msg，其余（网络层/HTTP 状态类）一律回退中文
const messageFrom = (e, fallback) => e?.response?.data?.msg || fallback;
const pageError = ref('');
const operationError = ref('');

let requestSeq = 0;

const loadProjects = async () => {
  try {
    const res = await axios.get(url('/api/projects'));
    projects.value = res.data?.data?.items || [];
    // 共享项目上下文：模块页首次载入时自动回选工作台/其他模块选中的项目（gui-audit #5）
    const sharedProjectId = getSelectedProjectId();
    if (!selectedProjectId.value && sharedProjectId && projects.value.some((p) => p.id === sharedProjectId)) {
      selectedProjectId.value = sharedProjectId;
      resetAndLoad();
    }
  } catch (_) {
    pageError.value = '项目列表读取失败';
  }
};

const loadProjectData = async () => {
  const seq = (requestSeq += 1);
  const projectId = selectedProjectId.value;
  if (!projectId) return;
  loading.value = true;
  pageError.value = '';
  operationError.value = '';
  try {
    const [overviewRes, exportsRes, snapshotsRes] = await Promise.all([
      unwrap(axios.get(url(`/api/projects/${projectId}/overview`))),
      unwrap(axios.get(url(`/api/projects/${projectId}/exports`))),
      unwrap(axios.get(url(`/api/projects/${projectId}/backups`))),
    ]);
    if (seq !== requestSeq) return;
    capabilities.value = overviewRes?.capabilities || {};
    // 两个列表端点的信封形态不同（数组或 {items}），统一归一
    exports.value = Array.isArray(exportsRes) ? exportsRes : (exportsRes?.items || []);
    snapshots.value = Array.isArray(snapshotsRes) ? snapshotsRes : (snapshotsRes?.items || []);
  } catch (e) {
    if (seq !== requestSeq) return;
    exports.value = [];
    snapshots.value = [];
    pageError.value = messageFrom(e, '项目数据读取失败，请稍后重试');
  } finally {
    if (seq === requestSeq) loading.value = false;
  }
};

const resetAndLoad = () => {
  setSelectedProjectId(Number(selectedProjectId.value) || null); // 用户手选同步共享上下文
  exports.value = [];
  snapshots.value = [];
  capabilities.value = {};
  loadProjectData();
};

const createExport = async (format) => {
  if (!selectedProjectId.value || busy.value) return;
  busy.value = true;
  operationError.value = '';
  try {
    await unwrap(axios.post(url(`/api/projects/${selectedProjectId.value}/exports`), { format }));
    await loadProjectData();
  } catch (e) {
    operationError.value = messageFrom(e, '导出创建失败');
  } finally {
    busy.value = false;
  }
};

const createSnapshot = async () => {
  if (!selectedProjectId.value || busy.value) return;
  busy.value = true;
  operationError.value = '';
  try {
    await unwrap(axios.post(url(`/api/projects/${selectedProjectId.value}/backups`), {}));
    await loadProjectData();
  } catch (e) {
    operationError.value = messageFrom(e, '项目配置快照创建失败');
  } finally {
    busy.value = false;
  }
};

const downloadFile = async (path, filename) => {
  downloading.value = true;
  operationError.value = '';
  try {
    const res = await axios.get(url(path), { responseType: 'blob' });
    const objectUrl = URL.createObjectURL(res.data);
    const anchor = document.createElement('a');
    anchor.href = objectUrl;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(objectUrl);
  } catch (e) {
    operationError.value = messageFrom(e, '下载失败，请稍后重试');
  } finally {
    downloading.value = false;
  }
};

const downloadExport = (item) => {
  if (!selectedProjectId.value || downloading.value) return;
  return downloadFile(
    `/api/projects/${selectedProjectId.value}/exports/${item.id}/artifact`,
    item.artifact_name || `export-${item.id}`,
  );
};

const downloadSnapshot = (item) => {
  if (!selectedProjectId.value || downloading.value) return;
  return downloadFile(
    `/api/projects/${selectedProjectId.value}/backups/${item.id}/manifest`,
    `snapshot-${item.id}-manifest.json`,
  );
};

const restoreSnapshot = async (snapshotId) => {
  if (!snapshotId || busy.value) return;
  if (!window.confirm(`确认从项目配置快照 ${snapshotId} 恢复项目索引吗？`)) return;
  busy.value = true;
  operationError.value = '';
  try {
    await unwrap(axios.post(url(`/api/projects/${selectedProjectId.value}/backups/${snapshotId}/restore`), {}));
    await loadProjectData();
  } catch (e) {
    operationError.value = messageFrom(e, '项目配置快照恢复失败');
  } finally {
    busy.value = false;
  }
};

loadProjects();
</script>

<style scoped>
.data-manager-view {
  max-width: 860px;
  margin: 0 auto;
  padding: 28px 24px;
  color: #264b45;
}
.view-header { margin-bottom: 18px; }
.view-header h1 { margin: 4px 0 6px; font-size: 22px; }
.kicker { color: #5a7d75; font-size: 13px; margin: 0; }
.muted-text { color: #5a7d75; font-size: 12px; }

.view-body {
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid rgba(38, 75, 69, 0.15);
  border-radius: 10px;
  padding: 18px 20px;
}

.import-hint { margin: 0 0 12px; }
.toolbar-row { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
.toolbar-row label { font-size: 14px; }
.toolbar-row select {
  padding: 7px 10px; border-radius: 6px;
  border: 1px solid rgba(38, 75, 69, 0.3); font-size: 13px;
}

.empty-block { margin: 18px 0; text-align: center; color: #5a7d75; font-size: 13px; }
</style>
