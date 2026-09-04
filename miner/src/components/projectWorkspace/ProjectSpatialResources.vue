<template>
  <section class="spatial-resources-panel">
    <div class="panel-title-row">
      <div>
        <h2>项目空间资源</h2>
        <p class="muted-text">矿山边界和离线底图只作用于当前项目。</p>
      </div>
      <button class="primary-btn" type="button" :disabled="!canConfigure" @click="toggleOpen">
        {{ open ? '收起空间资源' : '配置空间资源' }}
      </button>
    </div>

    <template v-if="open">
      <div class="wizard-steps" aria-label="空间资源配置步骤">
        <span :class="{ active: wizardStep === 1, done: wizardStep > 1 }">1 基本信息</span>
        <span :class="{ active: wizardStep === 2, done: wizardStep > 2 }">2 导入矿山</span>
        <span :class="{ active: wizardStep === 3, done: wizardStep > 3 }">3 选择底图</span>
        <span :class="{ active: wizardStep === 4 }">4 处理进度</span>
      </div>

      <section v-if="wizardStep === 2" class="wizard-panel">
        <label class="file-input-label">
          选择矿山文件
          <input type="file" accept=".kml,.geojson,.json" :disabled="busy || !canConfigure" @change="handleMineFile" />
        </label>
        <p class="muted-text">支持 KML、GeoJSON，最大 50 MB。预览确认后会导入文件中的全部矿山。</p>
        <p v-if="mineFileName" class="selected-file">已选择：{{ mineFileName }}</p>

        <div v-if="minePreview" class="mapping-grid">
          <p>识别到 {{ minePreview.feature_count }} 座矿山，CRS：{{ minePreview.crs || '--' }}</p>
          <label v-for="target in mappingTargets" :key="target.key">
            {{ target.label }}
            <select v-model="mineFieldMapping[target.key]" :disabled="busy || !canConfigure">
              <option value="">不映射</option>
              <option v-for="field in minePreview.field_names || []" :key="field" :value="field">
                {{ field }}
              </option>
            </select>
          </label>
          <button
            class="primary-btn"
            type="button"
            :disabled="busy || !canConfigure || !mineFileContent || !mineFieldMapping.fid"
            @click="importMine"
          >
            {{ busy ? '导入中…' : '确认导入全部矿山' }}
          </button>
        </div>
      </section>

      <section v-else-if="wizardStep === 3" class="wizard-panel">
        <div class="action-row">
          <button class="secondary-btn" type="button" :disabled="busy || !canConfigure" @click="$emit('load-basemap-candidates')">
            刷新导入目录
          </button>
          <span class="muted-text">请先将 TIF/TIFF 及同名辅助文件准备到项目离线导入目录。</span>
        </div>

        <p v-if="!basemapCandidates.length" class="muted-text">暂无可用底图候选。</p>
        <label v-for="item in basemapCandidates" :key="item.candidate" class="candidate-item">
          <input
            type="radio"
            name="basemap-candidate"
            :value="item.candidate"
            :checked="item.candidate === selectedBasemapCandidate"
            :disabled="busy || !canConfigure"
            @change="$emit('update:selected-basemap-candidate', item.candidate)"
          />
          <span>{{ item.filename || '未命名底图' }}（{{ formatFileSize(item.size_bytes) }}）</span>
          <small>{{ item.crs || '--' }} · {{ item.intersects_mines ? '与矿山范围相交' : '不相交' }}</small>
        </label>
        <button
          class="primary-btn"
          type="button"
          :disabled="busy || !canConfigure || !selectedBasemapCandidate"
          @click="registerBasemap"
        >
          {{ busy ? '提交中…' : '启动离线切片' }}
        </button>
      </section>

      <section v-else class="wizard-panel">
        <p v-if="spatial?.map_ready" class="success-text">矿山和底图均已激活，项目地图可用。</p>
        <template v-else-if="spatialJobs.length">
          <article v-for="job in spatialJobs" :key="job.id" class="job-item">
            <p>阶段：{{ job.stage || '--' }} · 状态：{{ job.status || '--' }}</p>
            <template v-if="isRunningJob(job)">
              <progress :value="progressValue(job.progress)" max="100"></progress>
              <span>{{ progressValue(job.progress).toFixed(1) }}%</span>
            </template>
            <p v-if="job.error_message" class="error-text">失败原因：{{ job.error_message }}</p>
            <div class="action-row">
              <button
                v-if="canRetryJob(job)"
                class="secondary-btn"
                type="button"
                :disabled="busy || !canConfigure"
                @click="$emit('retry-job', job.id)"
              >重试任务</button>
              <button
                v-if="canCancelJob(job)"
                class="ghost-btn"
                type="button"
                :disabled="busy || !canConfigure"
                @click="$emit('cancel-job', job.id)"
              >取消任务</button>
            </div>
          </article>
        </template>
        <p v-else class="muted-text">等待空间处理任务。</p>
      </section>

      <p v-if="displayError" class="error-text">{{ displayError }}</p>
    </template>

    <section v-if="mineOptions.length" class="mine-list-panel">
      <div class="panel-title-row">
        <h3>已绑定矿山</h3>
        <span class="muted-text">共 {{ mineOptions.length }} 座</span>
      </div>
      <div class="mine-list">
        <article v-for="item in mineOptions" :key="item.fid" class="mine-item">
          <div>
            <strong>{{ item.name || `矿山 ${item.fid}` }}</strong>
            <small>{{ item.city || '未标注区域' }}</small>
          </div>
          <button
            class="link-btn"
            type="button"
            :disabled="!spatial?.map_ready"
            @click="$emit('open-map', Number(item.fid))"
          >定位地图</button>
        </article>
      </div>
    </section>
  </section>
</template>

<script setup>
import { computed, ref, watch } from 'vue';

const MAX_MINE_FILE_BYTES = 50 * 1024 * 1024;
const mappingTargets = [
  { key: 'fid', label: '唯一 ID' },
  { key: 'name', label: '矿山名称' },
  { key: 'region', label: '区域' },
  { key: 'status', label: '状态' },
  { key: 'area', label: '面积' },
];

const props = defineProps({
  spatial: {
    type: Object,
    default: null,
  },
  mineOptions: {
    type: Array,
    default: () => [],
  },
  canConfigure: {
    type: Boolean,
    default: false,
  },
  busy: {
    type: Boolean,
    default: false,
  },
  error: {
    type: String,
    default: '',
  },
  minePreview: {
    type: Object,
    default: null,
  },
  basemapCandidates: {
    type: Array,
    default: () => [],
  },
  selectedBasemapCandidate: {
    type: String,
    default: '',
  },
  open: {
    type: Boolean,
    default: false,
  },
});

const emit = defineEmits([
  'update:open',
  'preview-mine',
  'clear-mine-preview',
  'import-mine',
  'load-basemap-candidates',
  'register-basemap',
  'retry-job',
  'cancel-job',
  'update:selected-basemap-candidate',
  'open-map',
]);

const wizardStep = ref(2);
const mineFileName = ref('');
const mineFileContent = ref('');
const localError = ref('');
const mineFieldMapping = ref(createEmptyMapping());

const spatialJobs = computed(() => Array.isArray(props.spatial?.jobs) ? props.spatial.jobs : []);
const displayError = computed(() => localError.value || props.error);

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) syncWizardStep({ refreshCandidates: true });
  },
);

watch(
  () => props.spatial,
  () => {
    if (props.open) syncWizardStep();
  },
);

watch(
  () => props.minePreview,
  (preview) => {
    if (!preview) return;
    mineFieldMapping.value = {
      ...createEmptyMapping(),
      ...(preview.suggested_mapping || {}),
    };
  },
  { immediate: true },
);

function createEmptyMapping() {
  return { fid: '', name: '', region: '', status: '', area: '' };
}

function toggleOpen() {
  emit('update:open', !props.open);
}

function syncWizardStep({ refreshCandidates = false } = {}) {
  const missingResources = props.spatial?.missing_resources;
  const hasActionableJob = spatialJobs.value.some((job) => (
    ['queued', 'running', 'failed', 'cancelled'].includes(job?.status)
  ));
  let nextStep = 4;
  if (hasActionableJob) {
    nextStep = 4;
  } else if (!Array.isArray(missingResources) || missingResources.includes('mine_vector')) {
    nextStep = 2;
  } else if (missingResources.includes('basemap')) {
    nextStep = 3;
  }
  const changedStep = wizardStep.value !== nextStep;
  wizardStep.value = nextStep;
  if (nextStep === 3 && (refreshCandidates || changedStep)) {
    emit('load-basemap-candidates');
  }
}

async function handleMineFile(event) {
  const file = event?.target?.files?.[0];
  if (!file) return;

  emit('clear-mine-preview');
  localError.value = '';
  mineFileName.value = '';
  mineFileContent.value = '';

  if (file.size > MAX_MINE_FILE_BYTES) {
    localError.value = '矿山文件不能超过 50 MB';
    event.target.value = '';
    return;
  }

  try {
    mineFileName.value = file.name;
    mineFileContent.value = await file.text();
    emit('preview-mine', {
      filename: mineFileName.value,
      content: mineFileContent.value,
    });
  } catch (error) {
    localError.value = error?.message || '矿山文件读取失败，请重新选择文件';
  }
}

function importMine() {
  emit('import-mine', {
    filename: mineFileName.value,
    content: mineFileContent.value,
    field_mapping: { ...mineFieldMapping.value },
  });
}

function registerBasemap() {
  emit('register-basemap', {
    candidate: props.selectedBasemapCandidate,
    min_zoom: 8,
    max_zoom: 15,
  });
}

function isRunningJob(job) {
  return ['queued', 'running'].includes(job?.status);
}

function canRetryJob(job) {
  return ['failed', 'cancelled'].includes(job?.status);
}

function canCancelJob(job) {
  return isRunningJob(job);
}

function progressValue(value) {
  const normalized = Number(value || 0);
  return Math.min(100, Math.max(0, Number.isFinite(normalized) ? normalized : 0));
}

function formatFileSize(value) {
  const bytes = Number(value || 0);
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(2)} MB`;
  return `${(bytes / 1024).toFixed(1)} KB`;
}
</script>

<style scoped>
.spatial-resources-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 18px;
  border: 1px solid rgba(35, 86, 78, 0.1);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.88);
  box-shadow: 0 12px 30px rgba(31, 66, 61, 0.08);
}

.panel-title-row,
.action-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.panel-title-row {
  justify-content: space-between;
}

.panel-title-row h2,
.panel-title-row h3,
.panel-title-row p {
  margin: 0;
}

.muted-text {
  color: #5d6f6d;
}

.wizard-steps {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
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

.file-input-label {
  display: flex;
  flex-direction: column;
  gap: 8px;
  font-weight: 600;
}

.selected-file {
  margin: 0;
  color: #1f5c4d;
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

.mapping-grid label {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.candidate-item {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
  padding: 10px;
  border-radius: 10px;
  background: #fff;
}

.candidate-item input {
  width: auto;
}

.job-item {
  padding: 12px;
  border-radius: 12px;
  border: 1px solid rgba(35, 86, 78, 0.08);
  background: #fff;
}

.job-item p {
  margin: 0 0 8px;
}

progress {
  width: 100%;
}

.mine-list-panel {
  padding-top: 14px;
  border-top: 1px solid rgba(35, 86, 78, 0.12);
}

.mine-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: min(60vh, 640px);
  margin-top: 12px;
  overflow-y: auto;
  padding-right: 6px;
  scrollbar-gutter: stable;
}

.mine-item {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  align-items: center;
  padding: 12px;
  border: 1px solid rgba(35, 86, 78, 0.08);
  border-radius: 12px;
  background: #f7faf8;
}

.mine-item div {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

input,
select,
button {
  font: inherit;
}

input,
select {
  box-sizing: border-box;
  border: 1px solid rgba(35, 86, 78, 0.15);
  border-radius: 12px;
  padding: 10px 12px;
  background: #fff;
  color: #163030;
}

button {
  padding: 10px 14px;
  border-radius: 12px;
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
}

.success-text {
  color: #237a59;
  font-weight: 600;
}

.error-text {
  color: #b43c2f;
  margin: 0;
}

@media (max-width: 700px) {
  .panel-title-row,
  .action-row,
  .mine-item {
    align-items: flex-start;
    flex-direction: column;
  }

  .wizard-steps,
  .mapping-grid,
  .candidate-item {
    grid-template-columns: 1fr;
  }
}
</style>
