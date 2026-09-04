<template>
  <div class="workspace-page">
    <header class="workspace-header">
      <div>
        <p class="workspace-kicker">矿山项目化监测平台</p>
        <h1>项目工作台</h1>
        <p class="workspace-subtitle">一期默认入口，承接项目建档、矿山绑定、数据登记和只读溯源。</p>
      </div>
      <div class="workspace-header-actions">
        <button v-if="username" class="ghost-btn" @click="$emit('logout')">{{ username }} 退出登录</button>
        <button
          class="secondary-btn"
          :disabled="!currentProjectDetail?.summary?.map_ready"
          @click="$emit('open-map', currentProjectId, null)"
        >
          {{ currentProjectDetail?.summary?.map_ready ? '打开矿山地图' : '地图资源未就绪' }}
        </button>
        <button class="primary-btn" @click="startCreateProject">新建项目</button>
      </div>
    </header>

    <section class="workspace-filters panel">
      <input v-model.trim="filters.name" placeholder="项目名称" />
      <input v-model.trim="filters.region" placeholder="区域" />
      <input v-model.trim="filters.monitorYear" placeholder="监测年份" />
      <select v-model="filters.status">
        <option value="">全部状态</option>
        <option v-for="status in projectStatuses" :key="status" :value="status">
          {{ formatProjectStatus(status) }}
        </option>
      </select>
      <button class="secondary-btn" @click="resetFilters">重置筛选</button>
    </section>

    <section class="workspace-layout">
      <aside class="panel project-list-panel">
        <div class="panel-title-row">
          <h2>项目列表</h2>
          <span>{{ loadingProjects ? '加载中...' : `${visibleProjects.length} / ${projects.length}` }}</span>
        </div>
        <p v-if="listError" class="error-text">{{ listError }}</p>
        <button class="ghost-btn" @click="loadProjects(currentProjectId)">刷新列表</button>
        <div class="project-card-list">
          <div v-if="loadingProjects && !projects.length" class="empty-block">项目列表加载中...</div>
          <template v-else>
            <button
              v-for="item in visibleProjects"
              :key="item.id"
              class="project-card"
              :class="{ active: item.id === currentProjectId }"
              @click="selectProject(item.id)"
            >
              <div class="project-card-top">
                <strong>{{ item.name }}</strong>
                <span class="status-pill" :data-status="item.status">{{ formatProjectStatus(item.status) }}</span>
              </div>
              <p>{{ item.region || '未填写区域' }}</p>
              <p>监测期：{{ formatYearRange(item.monitor_start_year, item.monitor_end_year) }}</p>
              <p>矿山 {{ item.mine_count || 0 }} 座，数据 {{ item.dataset_count || 0 }} 份</p>
              <p>地图：{{ formatSpatialStatus(item.spatial_status) }}</p>
            </button>
            <div v-if="!visibleProjects.length" class="empty-block">暂无匹配项目</div>
          </template>
        </div>
      </aside>

      <main class="project-main">
        <section class="panel" v-if="showProjectForm">
          <div class="panel-title-row">
            <h2>{{ editingProjectId ? '编辑项目' : '步骤 1/4：新建项目基本信息' }}</h2>
            <button class="ghost-btn" @click="cancelProjectForm">关闭</button>
          </div>
          <form class="project-form" @submit.prevent="submitProjectForm">
            <input v-model.trim="projectForm.name" placeholder="项目名称" required />
            <input v-model.trim="projectForm.region" placeholder="区域" />
            <input v-model.trim="projectForm.manager" placeholder="负责人" />
            <select v-model="projectForm.status">
              <option v-for="status in projectStatuses" :key="status" :value="status">
                {{ formatProjectStatus(status) }}
              </option>
            </select>
            <input v-model.number="projectForm.monitor_start_year" type="number" placeholder="开始年份" />
            <input v-model.number="projectForm.monitor_end_year" type="number" placeholder="结束年份" />
            <textarea v-model.trim="projectForm.remark" placeholder="备注"></textarea>
            <div class="form-actions">
              <button class="primary-btn" type="submit" :disabled="savingProject">
                {{ savingProject ? '保存中...' : '保存项目' }}
              </button>
            </div>
          </form>
          <p v-if="formError" class="error-text">{{ formError }}</p>
        </section>

        <section v-if="currentProjectDetail" class="project-detail-stack">
          <section class="panel">
            <div class="panel-title-row">
              <div>
                <h2>{{ currentProjectDetail.summary?.name }}</h2>
                <p class="muted-text">
                  {{ currentProjectDetail.summary?.region || '未填写区域' }}
                  · {{ formatYearRange(currentProjectDetail.summary?.monitor_start_year, currentProjectDetail.summary?.monitor_end_year) }}
                </p>
              </div>
              <div class="action-row">
                <button class="secondary-btn" @click="startEditCurrentProject">编辑项目</button>
                <button class="secondary-btn" @click="refreshCurrentProject">刷新详情</button>
                <button
                  v-if="currentProjectDetail.summary?.status !== 'archived'"
                  class="ghost-btn"
                  @click="archiveCurrentProject"
                >
                  归档
                </button>
                <button v-else class="ghost-btn" @click="restoreCurrentProject">恢复</button>
              </div>
            </div>
            <div class="summary-grid">
              <div class="summary-card">
                <span>状态</span>
                <strong>{{ formatProjectStatus(currentProjectDetail.summary?.status) }}</strong>
              </div>
              <div class="summary-card">
                <span>负责人</span>
                <strong>{{ currentProjectDetail.summary?.manager || '未填写' }}</strong>
              </div>
              <div class="summary-card">
                <span>矿山数量</span>
                <strong>{{ currentProjectDetail.summary?.mine_count || 0 }}</strong>
              </div>
              <div class="summary-card">
                <span>数据集数量</span>
                <strong>{{ currentProjectDetail.summary?.dataset_count || 0 }}</strong>
              </div>
            </div>
            <p class="muted-text">{{ currentProjectDetail.summary?.remark || '暂无备注' }}</p>
          </section>

          <section class="panel">
            <div class="panel-title-row">
              <div>
                <h2>项目空间资源向导</h2>
                <p class="muted-text">每个项目独立导入矿山和离线底图，不会继承其他项目的数据。</p>
              </div>
              <button class="primary-btn" @click="openSpatialWizard">配置空间资源</button>
            </div>
            <div class="wizard-steps">
              <span :class="{ active: wizardStep === 1, done: wizardStep > 1 }">1 基本信息</span>
              <span :class="{ active: wizardStep === 2, done: wizardStep > 2 }">2 导入矿山</span>
              <span :class="{ active: wizardStep === 3, done: wizardStep > 3 }">3 选择底图</span>
              <span :class="{ active: wizardStep === 4 }">4 处理进度</span>
            </div>

            <div v-if="spatialWizardOpen && wizardStep === 2" class="wizard-panel">
              <input type="file" accept=".kml,.geojson,.json" @change="handleMineFile" />
              <p class="muted-text">支持 KML、GeoJSON，最大 50 MB。导入成功后自动绑定文件中的全部矿山。</p>
              <div v-if="minePreview" class="mapping-grid">
                <p>识别到 {{ minePreview.feature_count }} 座矿山，CRS：{{ minePreview.crs }}</p>
                <label v-for="target in mappingTargets" :key="target.key">
                  {{ target.label }}
                  <select v-model="mineFieldMapping[target.key]">
                    <option value="">不映射</option>
                    <option v-for="field in minePreview.field_names" :key="field" :value="field">{{ field }}</option>
                  </select>
                </label>
                <button class="primary-btn" :disabled="spatialBusy || !mineFieldMapping.fid" @click="importMineResource">
                  {{ spatialBusy ? '导入中…' : '确认导入全部矿山' }}
                </button>
              </div>
            </div>

            <div v-if="spatialWizardOpen && wizardStep === 3" class="wizard-panel">
              <div class="action-row">
                <button class="secondary-btn" :disabled="spatialBusy" @click="loadBasemapCandidates">刷新导入目录</button>
                <span class="muted-text">请先把 TIF/TIFF 及同名辅助文件复制到 project_storage/incoming。</span>
              </div>
              <label v-for="item in basemapCandidates" :key="item.candidate" class="candidate-item">
                <input v-model="selectedBasemapCandidate" type="radio" :value="item.candidate" />
                <span>{{ item.filename }}（{{ formatFileSize(item.size_bytes) }}）</span>
                <small>{{ item.crs }} · {{ item.intersects_mines ? '与矿山范围相交' : '不相交' }}</small>
              </label>
              <button
                class="primary-btn"
                :disabled="spatialBusy || !selectedBasemapCandidate"
                @click="startBasemapProcessing"
              >启动离线切片</button>
            </div>

            <div v-if="spatialWizardOpen && wizardStep === 4" class="wizard-panel">
              <p v-if="spatialInfo?.map_ready" class="success-text">矿山和底图均已激活，项目地图可用。</p>
              <template v-else-if="activeSpatialJob">
                <p>阶段：{{ activeSpatialJob.stage }} · 状态：{{ activeSpatialJob.status }}</p>
                <progress :value="activeSpatialJob.progress || 0" max="100"></progress>
                <span>{{ Number(activeSpatialJob.progress || 0).toFixed(1) }}%</span>
              </template>
              <p v-else class="muted-text">等待空间处理任务。</p>
            </div>
            <p v-if="spatialError" class="error-text">{{ spatialError }}</p>
          </section>

          <section class="panel">
            <div class="panel-title-row">
              <h2>绑定矿山</h2>
              <span class="muted-text">由项目矿山文件自动绑定，共 {{ mineOptions.length }} 座</span>
            </div>
            <div class="mine-list">
              <label v-for="item in mineOptions" :key="item.fid" class="mine-item">
                <span>{{ item.name }}</span>
                <small>{{ item.city || '未标注区域' }}</small>
                <button
                  class="link-btn"
                  type="button"
                  :disabled="!currentProjectDetail?.summary?.map_ready"
                  @click.stop="$emit('open-map', currentProjectId, Number(item.fid))"
                >定位地图</button>
              </label>
            </div>
          </section>

          <section class="panel">
            <div class="panel-title-row">
              <h2>项目数据集</h2>
              <span>{{ currentProjectDetail.datasets?.length || 0 }} 条</span>
            </div>
            <form class="dataset-form" @submit.prevent="submitDatasetForm">
              <input v-model.trim="datasetForm.display_name" placeholder="显示名称" required />
              <select v-model="datasetForm.dataset_kind">
                <option v-for="item in datasetKinds" :key="item" :value="item">
                  {{ formatDatasetKind(item) }}
                </option>
              </select>
              <input v-model.trim="datasetForm.file_path" placeholder="文件路径" required />
              <input v-model.trim="datasetForm.source_format" placeholder="源格式，例如 tif / xlsx" />
              <select v-model="datasetForm.mine_fid">
                <option value="">关联全部矿山/不指定</option>
                <option v-for="item in boundMineOptions" :key="item.fid" :value="item.fid">{{ item.name }}</option>
              </select>
              <input v-model.number="datasetForm.year_start" type="number" placeholder="开始年份" />
              <input v-model.number="datasetForm.year_end" type="number" placeholder="结束年份" />
              <textarea
                v-model.trim="datasetForm.slice_config_text"
                placeholder='切片参数 JSON，例如 {"slice_size":1024,"padding":64}'
              ></textarea>
              <div class="form-actions">
                <button class="primary-btn" type="submit" :disabled="savingDataset">
                  {{ savingDataset ? '登记中...' : '登记数据集' }}
                </button>
              </div>
            </form>
            <p v-if="datasetError" class="error-text">{{ datasetError }}</p>
            <div class="table-list">
              <div v-for="dataset in currentProjectDetail.datasets || []" :key="dataset.id" class="table-row">
                <strong>{{ dataset.display_name }}</strong>
                <span>{{ formatDatasetKind(dataset.dataset_kind) }}</span>
                <span>{{ dataset.year_start || '--' }} - {{ dataset.year_end || '--' }}</span>
                <span class="truncate-text">{{ dataset.file_path }}</span>
              </div>
            </div>
          </section>

          <section class="panel">
            <div class="panel-title-row">
              <h2>项目时间线</h2>
              <span>{{ currentProjectDetail.timeline?.length || 0 }} 条</span>
            </div>
            <div class="timeline-list">
              <div v-for="item in currentProjectDetail.timeline || []" :key="item.id" class="timeline-item">
                <strong>{{ formatTimelineType(item.event_type) }}</strong>
                <span>{{ item.actor || 'system' }}</span>
                <small>{{ item.timestamp }}</small>
              </div>
            </div>
          </section>

          <section class="panel">
            <div class="panel-title-row">
              <h2>导出与备份</h2>
              <div class="action-row">
                <button class="secondary-btn" @click="createExport('geojson')">导出 GeoJSON</button>
                <button class="secondary-btn" @click="createExport('csv')">导出 CSV</button>
                <button class="secondary-btn" @click="createExport('shp')">导出 SHP</button>
                <button class="primary-btn" @click="createBackup">生成备份</button>
              </div>
            </div>
            <input v-model.trim="outputDir" placeholder="可选输出目录，不填则走后端默认目录" />
            <div class="sub-panel">
              <h3>导出记录</h3>
              <div class="table-list">
                <div v-for="item in currentProjectDetail.exports || []" :key="item.id" class="table-row">
                  <strong>{{ String(item.format || '').toUpperCase() }}</strong>
                  <span>{{ item.status }}</span>
                  <span class="truncate-text">{{ item.file_path || '--' }}</span>
                </div>
              </div>
            </div>
            <div class="sub-panel">
              <h3>备份记录</h3>
              <div class="table-list">
                <div v-for="item in currentProjectDetail.backups || []" :key="item.id" class="table-row">
                  <strong>{{ item.scope }}</strong>
                  <span>{{ item.status }}</span>
                  <span class="truncate-text">{{ item.manifest_path || '--' }}</span>
                  <button class="link-btn" @click="restoreBackup(item.id)">恢复</button>
                </div>
              </div>
            </div>
          </section>
        </section>

        <section v-else-if="loadingProjects" class="panel empty-detail">
          <h2>项目加载中</h2>
          <p>正在读取项目列表和详情...</p>
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
import axios from 'axios';
import { computed, onMounted, onUnmounted, ref } from 'vue';

import { buildMineSelectionSet, filterProjects } from '../projectWorkspace/projectWorkspaceHelpers.js';

defineProps({
  username: {
    type: String,
    default: '',
  },
});

defineEmits(['open-map', 'logout']);

const rawMinerApiBase = import.meta.env.VITE_MINER_API_BASE_URL;
const MINER_API_BASE_URL = rawMinerApiBase ? String(rawMinerApiBase).replace(/\/$/, '') : '';
const apiUrl = (path) => `${MINER_API_BASE_URL}${path}`;

const projectStatuses = ['draft', 'active', 'completed', 'archived'];
const datasetKinds = ['imagery', 'inference_result', 'report', 'export_package'];
const projectStatusLabels = {
  draft: '草稿',
  active: '进行中',
  completed: '已完成',
  archived: '已归档'
};
const datasetKindLabels = {
  imagery: '影像',
  inference_result: '解译结果',
  report: '报告',
  export_package: '导出包'
};
const timelineTypeLabels = {
  create: '创建项目',
  update: '更新项目',
  bind_mines: '绑定矿山',
  import_dataset: '登记数据集',
  run_inference: '运行解译',
  create_export: '创建导出',
  create_backup: '生成备份',
  archive: '项目归档',
  restore: '项目恢复',
  restore_backup: '恢复备份'
};

const filters = ref({
  name: '',
  region: '',
  status: '',
  monitorYear: ''
});

const projects = ref([]);
const currentProjectId = ref(null);
const currentProjectDetail = ref(null);
const loadingProjects = ref(false);
const mineOptions = ref([]);
const selectedMineFids = ref([]);
const outputDir = ref('');

const showProjectForm = ref(false);
const editingProjectId = ref(null);
const savingProject = ref(false);
const savingMineBindings = ref(false);
const savingDataset = ref(false);

const listError = ref('');
const formError = ref('');
const datasetError = ref('');
const spatialWizardOpen = ref(false);
const wizardStep = ref(1);
const spatialBusy = ref(false);
const spatialError = ref('');
const spatialInfo = ref(null);
const activeSpatialJob = ref(null);
const mineFileName = ref('');
const mineFileContent = ref('');
const minePreview = ref(null);
const mineFieldMapping = ref({ fid: '', name: '', region: '', status: '', area: '' });
const basemapCandidates = ref([]);
const selectedBasemapCandidate = ref('');
const mappingTargets = [
  { key: 'fid', label: '唯一 ID' },
  { key: 'name', label: '矿山名称' },
  { key: 'region', label: '区域' },
  { key: 'status', label: '状态' },
  { key: 'area', label: '面积' }
];
let spatialPollTimer = null;

const projectForm = ref(createDefaultProjectForm());
const datasetForm = ref(createDefaultDatasetForm());

function createDefaultProjectForm() {
  return {
    name: '',
    region: '',
    manager: '',
    remark: '',
    status: 'draft',
    monitor_start_year: '',
    monitor_end_year: ''
  };
}

function createDefaultDatasetForm() {
  return {
    display_name: '',
    dataset_kind: 'imagery',
    file_path: '',
    source_format: '',
    mine_fid: '',
    year_start: '',
    year_end: '',
    slice_config_text: ''
  };
}

function unwrapPayload(response) {
  return response?.data?.data ?? response?.data ?? null;
}

function unwrapOrThrow(response) {
  if (response?.data?.success === false) {
    throw new Error(response.data.msg || '请求失败');
  }
  return unwrapPayload(response);
}

function formatFileSize(value) {
  const bytes = Number(value || 0);
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(2)} MB`;
  return `${(bytes / 1024).toFixed(1)} KB`;
}

function formatYearRange(startYear, endYear) {
  if (!startYear && !endYear) return '未设置';
  return `${startYear || '--'} - ${endYear || '--'}`;
}

function formatProjectStatus(status) {
  return projectStatusLabels[status] || status || '--';
}

function formatSpatialStatus(status) {
  return {
    unconfigured: '未配置',
    processing: '处理中',
    ready: '地图可用',
    failed: '处理失败'
  }[status] || '未配置';
}

function formatDatasetKind(kind) {
  return datasetKindLabels[kind] || kind || '--';
}

function formatTimelineType(type) {
  return timelineTypeLabels[type] || type || '--';
}

const visibleProjects = computed(() => filterProjects(projects.value, filters.value));

const boundMineOptions = computed(() => {
  const mines = Array.isArray(currentProjectDetail.value?.mines) ? currentProjectDetail.value.mines : [];
  return mines.map((item) => ({
    fid: String(item.mine_fid),
    name: item.mine_name_snapshot || `矿山 ${item.mine_fid}`
  }));
});

async function loadMineOptions(projectId) {
  if (!projectId) {
    mineOptions.value = [];
    return;
  }
  const data = unwrapPayload(await axios.get(apiUrl(`/api/projects/${projectId}/geojson`)));
  const features = Array.isArray(data?.features) ? data.features : [];
  mineOptions.value = features.map((feature) => {
    const properties = feature.properties || {};
    return {
      fid: String(properties.FID_1),
      name: properties.mine_name || properties.name || `矿山 ${properties.FID_1}`,
      city: properties.SHI || '',
      area: properties.area || properties.TBTYMJ || null,
      status: properties.status_normalized || ''
    };
  });
}

async function loadProjects(preferredProjectId = null) {
  listError.value = '';
  loadingProjects.value = true;
  try {
    const data = unwrapPayload(await axios.get(apiUrl('/api/projects')));
    projects.value = Array.isArray(data?.items) ? data.items : [];
    const nextProjectId = preferredProjectId || currentProjectId.value || projects.value[0]?.id || null;
    if (nextProjectId) {
      await selectProject(nextProjectId);
    } else {
      currentProjectId.value = null;
      currentProjectDetail.value = null;
    }
  } catch (error) {
    listError.value = error?.response?.data?.msg || error?.message || '项目列表加载失败';
  } finally {
    loadingProjects.value = false;
  }
}

async function selectProject(projectId) {
  if (!projectId) return;
  currentProjectId.value = Number(projectId);
  const requests = await Promise.allSettled([
    axios.get(apiUrl(`/api/projects/${projectId}`)),
    axios.get(apiUrl(`/api/projects/${projectId}/timeline`)),
    axios.get(apiUrl(`/api/projects/${projectId}/exports`)),
    axios.get(apiUrl(`/api/projects/${projectId}/backups`))
  ]);

  const detail = requests[0].status === 'fulfilled' ? unwrapPayload(requests[0].value) : null;
  if (!detail) {
    currentProjectDetail.value = null;
    return;
  }

  const timeline = requests[1].status === 'fulfilled'
    ? unwrapPayload(requests[1].value)?.items || []
    : detail.timeline || [];
  const exportsList = requests[2].status === 'fulfilled'
    ? unwrapPayload(requests[2].value)?.items || []
    : detail.exports || [];
  const backupsList = requests[3].status === 'fulfilled'
    ? unwrapPayload(requests[3].value)?.items || []
    : detail.backups || [];

  currentProjectDetail.value = {
    ...detail,
    timeline,
    exports: exportsList,
    backups: backupsList
  };
  try {
    await loadMineOptions(projectId);
  } catch (_) {
    mineOptions.value = [];
  }
  selectedMineFids.value = Array.from(buildMineSelectionSet(currentProjectDetail.value));
  datasetError.value = '';
}

function resetFilters() {
  filters.value = {
    name: '',
    region: '',
    status: '',
    monitorYear: ''
  };
}

function startCreateProject() {
  editingProjectId.value = null;
  projectForm.value = createDefaultProjectForm();
  showProjectForm.value = true;
  formError.value = '';
}

function startEditCurrentProject() {
  if (!currentProjectDetail.value?.summary) return;
  editingProjectId.value = currentProjectDetail.value.summary.id;
  projectForm.value = {
    name: currentProjectDetail.value.summary.name || '',
    region: currentProjectDetail.value.summary.region || '',
    manager: currentProjectDetail.value.summary.manager || '',
    remark: currentProjectDetail.value.summary.remark || '',
    status: currentProjectDetail.value.summary.status || 'draft',
    monitor_start_year: currentProjectDetail.value.summary.monitor_start_year || '',
    monitor_end_year: currentProjectDetail.value.summary.monitor_end_year || ''
  };
  showProjectForm.value = true;
  formError.value = '';
}

function cancelProjectForm() {
  showProjectForm.value = false;
  editingProjectId.value = null;
  formError.value = '';
}

async function submitProjectForm() {
  savingProject.value = true;
  formError.value = '';
  try {
    const payload = {
      ...projectForm.value,
      monitor_start_year: projectForm.value.monitor_start_year || null,
      monitor_end_year: projectForm.value.monitor_end_year || null
    };
    const response = editingProjectId.value
      ? await axios.patch(apiUrl(`/api/projects/${editingProjectId.value}`), payload)
      : await axios.post(apiUrl('/api/projects'), payload);
    const project = unwrapPayload(response);
    showProjectForm.value = false;
    await loadProjects(project?.id || editingProjectId.value);
    if (!editingProjectId.value && project?.id) {
      spatialWizardOpen.value = true;
      wizardStep.value = 2;
    }
  } catch (error) {
    formError.value = error?.response?.data?.msg || error?.message || '项目保存失败';
  } finally {
    savingProject.value = false;
  }
}

async function loadSpatialInfo() {
  if (!currentProjectId.value) return null;
  const data = unwrapOrThrow(await axios.get(apiUrl(`/api/projects/${currentProjectId.value}/spatial`)));
  spatialInfo.value = data;
  activeSpatialJob.value = (data?.jobs || []).find((job) => ['queued', 'running'].includes(job.status))
    || (data?.jobs || [])[0]
    || null;
  return data;
}

async function openSpatialWizard() {
  spatialWizardOpen.value = true;
  spatialError.value = '';
  try {
    const info = await loadSpatialInfo();
    if ((info?.missing_resources || []).includes('mine_vector')) wizardStep.value = 2;
    else if ((info?.missing_resources || []).includes('basemap')) {
      wizardStep.value = 3;
      await loadBasemapCandidates();
    } else wizardStep.value = 4;
  } catch (error) {
    spatialError.value = error?.response?.data?.msg || error?.message || '空间资源读取失败';
  }
}

async function handleMineFile(event) {
  const file = event?.target?.files?.[0];
  if (!file) return;
  spatialBusy.value = true;
  spatialError.value = '';
  minePreview.value = null;
  try {
    mineFileName.value = file.name;
    mineFileContent.value = await file.text();
    const response = await axios.post(apiUrl(`/api/projects/${currentProjectId.value}/spatial/mines/preview`), {
      filename: mineFileName.value,
      content: mineFileContent.value
    });
    minePreview.value = unwrapOrThrow(response);
    mineFieldMapping.value = { ...mineFieldMapping.value, ...(minePreview.value?.suggested_mapping || {}) };
  } catch (error) {
    spatialError.value = error?.response?.data?.msg || error?.message || '矿山文件预览失败';
  } finally {
    spatialBusy.value = false;
  }
}

async function importMineResource() {
  spatialBusy.value = true;
  spatialError.value = '';
  try {
    unwrapOrThrow(await axios.post(apiUrl(`/api/projects/${currentProjectId.value}/spatial/mines`), {
      filename: mineFileName.value,
      content: mineFileContent.value,
      field_mapping: mineFieldMapping.value
    }));
    await selectProject(currentProjectId.value);
    wizardStep.value = 3;
    await loadBasemapCandidates();
  } catch (error) {
    spatialError.value = error?.response?.data?.msg || error?.message || '矿山导入失败';
  } finally {
    spatialBusy.value = false;
  }
}

async function loadBasemapCandidates() {
  spatialBusy.value = true;
  spatialError.value = '';
  try {
    const response = await axios.get(apiUrl(`/api/projects/${currentProjectId.value}/spatial/basemap-candidates`));
    const data = unwrapOrThrow(response);
    basemapCandidates.value = data?.items || [];
    selectedBasemapCandidate.value = basemapCandidates.value.find((item) => item.intersects_mines)?.candidate || '';
  } catch (error) {
    basemapCandidates.value = [];
    spatialError.value = error?.response?.data?.msg || error?.message || '底图候选读取失败';
  } finally {
    spatialBusy.value = false;
  }
}

function scheduleSpatialPoll() {
  if (spatialPollTimer) window.clearTimeout(spatialPollTimer);
  spatialPollTimer = window.setTimeout(async () => {
    try {
      const info = await loadSpatialInfo();
      await selectProject(currentProjectId.value);
      if (!info?.map_ready && activeSpatialJob.value && ['queued', 'running'].includes(activeSpatialJob.value.status)) {
        scheduleSpatialPoll();
      }
    } catch (error) {
      spatialError.value = error?.response?.data?.msg || error?.message || '处理进度读取失败';
    }
  }, 2000);
}

async function startBasemapProcessing() {
  spatialBusy.value = true;
  spatialError.value = '';
  try {
    const response = await axios.post(apiUrl(`/api/projects/${currentProjectId.value}/spatial/basemaps`), {
      candidate: selectedBasemapCandidate.value,
      min_zoom: 8,
      max_zoom: 15
    });
    const data = unwrapOrThrow(response);
    activeSpatialJob.value = data?.job || null;
    wizardStep.value = 4;
    scheduleSpatialPoll();
  } catch (error) {
    spatialError.value = error?.response?.data?.msg || error?.message || '底图处理启动失败';
  } finally {
    spatialBusy.value = false;
  }
}

async function saveMineBindings() {
  if (!currentProjectId.value) return;
  savingMineBindings.value = true;
  try {
    const mineMap = new Map(mineOptions.value.map((item) => [item.fid, item]));
    const mines = selectedMineFids.value.map((mineFid, index) => {
      const mine = mineMap.get(String(mineFid)) || {};
      return {
        mine_fid: Number(mineFid),
        mine_name_snapshot: mine.name || `矿山 ${mineFid}`,
        city_snapshot: mine.city || '',
        area_snapshot: mine.area,
        status_snapshot: mine.status || '',
        sort_order: index + 1
      };
    });
    await axios.put(apiUrl(`/api/projects/${currentProjectId.value}/mines`), { mines });
    await refreshCurrentProject();
  } catch (error) {
    alert(error?.response?.data?.msg || error?.message || '矿山绑定保存失败');
  } finally {
    savingMineBindings.value = false;
  }
}

async function submitDatasetForm() {
  if (!currentProjectId.value) return;
  savingDataset.value = true;
  datasetError.value = '';
  try {
    let sliceConfigJson = {};
    if (datasetForm.value.slice_config_text) {
      sliceConfigJson = JSON.parse(datasetForm.value.slice_config_text);
    }
    const payload = {
      display_name: datasetForm.value.display_name,
      dataset_kind: datasetForm.value.dataset_kind,
      file_path: datasetForm.value.file_path,
      source_format: datasetForm.value.source_format || null,
      mine_fid: datasetForm.value.mine_fid ? Number(datasetForm.value.mine_fid) : null,
      year_start: datasetForm.value.year_start || null,
      year_end: datasetForm.value.year_end || null,
      slice_config_json: sliceConfigJson
    };
    await axios.post(apiUrl(`/api/projects/${currentProjectId.value}/datasets`), payload);
    datasetForm.value = createDefaultDatasetForm();
    await refreshCurrentProject();
  } catch (error) {
    datasetError.value = error?.response?.data?.msg || error?.message || '数据集登记失败';
  } finally {
    savingDataset.value = false;
  }
}

async function refreshCurrentProject() {
  if (!currentProjectId.value) return;
  await selectProject(currentProjectId.value);
  await loadProjects(currentProjectId.value);
}

async function archiveCurrentProject() {
  if (!currentProjectId.value) return;
  await axios.post(apiUrl(`/api/projects/${currentProjectId.value}/archive`), {});
  await refreshCurrentProject();
}

async function restoreCurrentProject() {
  if (!currentProjectId.value) return;
  await axios.post(apiUrl(`/api/projects/${currentProjectId.value}/restore`), {});
  await refreshCurrentProject();
}

async function createExport(format) {
  if (!currentProjectId.value) return;
  await axios.post(apiUrl(`/api/projects/${currentProjectId.value}/exports`), {
    format,
    output_dir: outputDir.value || null
  });
  await refreshCurrentProject();
}

async function createBackup() {
  if (!currentProjectId.value) return;
  await axios.post(apiUrl(`/api/projects/${currentProjectId.value}/backups`), {
    scope: 'metadata_index',
    output_dir: outputDir.value || null
  });
  await refreshCurrentProject();
}

async function restoreBackup(backupId) {
  if (!currentProjectId.value || !backupId) return;
  if (!window.confirm(`确认从备份 ${backupId} 恢复项目索引吗？`)) return;
  await axios.post(apiUrl(`/api/projects/${currentProjectId.value}/backups/${backupId}/restore`), {});
  await refreshCurrentProject();
}

onMounted(async () => {
  await loadProjects();
});

onUnmounted(() => {
  if (spatialPollTimer) window.clearTimeout(spatialPollTimer);
});
</script>

<style scoped>
.workspace-page {
  min-height: 100vh;
  padding: 24px;
  background:
    radial-gradient(circle at top left, rgba(18, 125, 110, 0.18), transparent 30%),
    linear-gradient(180deg, #eef4f1 0%, #f7faf8 100%);
  color: #163030;
  box-sizing: border-box;
}

.workspace-header {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
  margin-bottom: 20px;
}

.workspace-header h1,
.panel h2,
.sub-panel h3 {
  margin: 0;
}

.workspace-kicker {
  margin: 0 0 8px;
  color: #2f7a68;
  font-size: 13px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.workspace-subtitle,
.muted-text {
  color: #5d6f6d;
}

.workspace-header-actions,
.action-row,
.form-actions,
.panel-title-row {
  display: flex;
  gap: 10px;
  align-items: center;
}

.workspace-filters,
.workspace-layout {
  display: flex;
  gap: 16px;
}

.workspace-filters {
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.workspace-layout {
  align-items: flex-start;
}

.panel {
  background: rgba(255, 255, 255, 0.88);
  border: 1px solid rgba(35, 86, 78, 0.1);
  border-radius: 18px;
  box-shadow: 0 12px 30px rgba(31, 66, 61, 0.08);
  padding: 18px;
  box-sizing: border-box;
}

.project-list-panel {
  width: 320px;
  flex-shrink: 0;
}

.project-main,
.project-detail-stack {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.project-card-list,
.timeline-list,
.table-list,
.mine-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 12px;
}

.mine-list {
  max-height: min(60vh, 640px);
  overflow-y: auto;
  padding-right: 6px;
  scrollbar-gutter: stable;
}

.project-card {
  border: 1px solid rgba(35, 86, 78, 0.14);
  border-radius: 14px;
  padding: 14px;
  background: #f8fcfa;
  text-align: left;
  cursor: pointer;
}

.project-card.active {
  border-color: #2f7a68;
  background: #edf7f3;
}

.project-card p,
.empty-block {
  margin: 6px 0 0;
}

.project-card-top,
.summary-grid {
  display: flex;
  gap: 10px;
}

.project-card-top {
  justify-content: space-between;
  align-items: center;
}

.summary-grid {
  flex-wrap: wrap;
  margin: 16px 0;
}

.summary-card {
  min-width: 150px;
  flex: 1;
  border-radius: 14px;
  background: #f4faf7;
  border: 1px solid rgba(35, 86, 78, 0.08);
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.wizard-steps {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  margin: 16px 0;
}

.wizard-steps span {
  padding: 10px;
  border-radius: 10px;
  background: #edf2f0;
  color: #61716e;
  text-align: center;
}

.wizard-steps span.active,
.wizard-steps span.done {
  background: #dff1ea;
  color: #236b59;
}

.wizard-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px;
  border: 1px solid rgba(35, 86, 78, 0.12);
  border-radius: 14px;
  background: #f8fcfa;
}

.mapping-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.mapping-grid > p,
.mapping-grid > button {
  grid-column: 1 / -1;
}

.candidate-item {
  display: grid;
  grid-template-columns: auto 1fr auto;
  gap: 10px;
  align-items: center;
  padding: 10px;
  border-radius: 10px;
  background: #fff;
}

.candidate-item input {
  width: auto;
}

.wizard-panel progress {
  width: 100%;
}

.success-text {
  color: #237a59;
  font-weight: 600;
}

.project-form,
.dataset-form {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-top: 14px;
}

.project-form textarea,
.dataset-form textarea,
.project-form .form-actions,
.dataset-form .form-actions {
  grid-column: 1 / -1;
}

.mine-item,
.timeline-item,
.table-row {
  display: grid;
  grid-template-columns: auto 1fr auto auto;
  gap: 10px;
  align-items: center;
  padding: 12px;
  border-radius: 12px;
  background: #f7faf8;
  border: 1px solid rgba(35, 86, 78, 0.08);
}

.timeline-item {
  grid-template-columns: 1.3fr 0.7fr 1fr;
}

.table-row {
  grid-template-columns: 1fr auto auto 1.3fr auto;
}

.sub-panel + .sub-panel {
  margin-top: 14px;
}

input,
select,
textarea,
button {
  font: inherit;
}

input,
select,
textarea {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid rgba(35, 86, 78, 0.15);
  border-radius: 12px;
  padding: 10px 12px;
  background: #fff;
  color: #163030;
}

textarea {
  min-height: 92px;
  resize: vertical;
}

button {
  border-radius: 12px;
  padding: 10px 14px;
  cursor: pointer;
}

.primary-btn {
  border: none;
  background: #2f7a68;
  color: #fff;
}

.secondary-btn {
  border: 1px solid rgba(47, 122, 104, 0.2);
  background: #eaf6f1;
  color: #1f5c4d;
}

.ghost-btn,
.link-btn {
  border: none;
  background: transparent;
  color: #2f7a68;
  padding: 0;
}

.status-pill {
  padding: 4px 8px;
  border-radius: 999px;
  font-size: 12px;
  background: #edf7f3;
  color: #1f5c4d;
}

.status-pill[data-status='archived'] {
  background: #eef0f3;
  color: #5d6570;
}

.status-pill[data-status='completed'] {
  background: #eef5ff;
  color: #28508a;
}

.error-text {
  color: #b43c2f;
  margin: 12px 0 0;
}

.truncate-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.empty-detail {
  min-height: 300px;
  display: flex;
  flex-direction: column;
  justify-content: center;
}

@media (max-width: 1100px) {
  .workspace-header,
  .workspace-layout {
    flex-direction: column;
  }

  .project-list-panel {
    width: 100%;
  }

  .project-form,
  .dataset-form {
    grid-template-columns: 1fr;
  }

  .mine-item,
  .table-row,
  .timeline-item {
    grid-template-columns: 1fr;
  }
}
</style>
