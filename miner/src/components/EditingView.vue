<template>
  <div class="editing-view">
    <header class="editing-header">
      <div>
        <p class="workspace-kicker">图斑编辑</p>
        <h1>分类成果修订</h1>
        <p class="muted-text">选择项目与成果年份，跳转 GeoView 编辑器完成矢量修订（保存为新修订版本）。</p>
      </div>
    </header>

    <section class="editing-body">
      <div class="editing-row">
        <label for="editing-project">项目</label>
        <select id="editing-project" v-model="selectedProjectId" @change="loadResults">
          <option :value="null" disabled>请选择项目</option>
          <option v-for="project in projects" :key="project.id" :value="project.id">
            {{ project.name }}（{{ project.status }}）
          </option>
        </select>
      </div>

      <p v-if="loading" class="empty-block">成果清单加载中…</p>
      <p v-else-if="error" class="error-text">{{ error }}</p>
      <p v-else-if="!selectedProjectId" class="empty-block">先选择项目</p>
      <p v-else-if="!results.length" class="empty-block">该项目暂无分类成果（需先在智能解译中完成推理）</p>
      <div v-else class="result-list">
        <div v-for="row in results" :key="row.key" class="result-row">
          <div class="result-main">
            <strong>矿山 {{ row.fid }} · {{ row.year ?? '未知年份' }}</strong>
            <span class="muted-text">
              图斑 {{ row.featureCount }} 个 · {{ statusText(row.vectorStatus) }} · 修订 v{{ row.currentRevisionNo ?? 0 }}
            </span>
          </div>
          <button
            class="primary-btn"
            type="button"
            :disabled="!row.editable"
            :title="row.editable ? '在 GeoView 编辑器中打开' : '矢量未就绪，不能编辑'"
            @click="openEditor(row)"
          >
            {{ row.editable ? '编辑矢量' : '不可编辑' }}
          </button>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import axios from 'axios';
import { ref } from 'vue';

const MINER_API_BASE_URL = import.meta.env.VITE_MINER_API_BASE_URL || '';
const GEOVIEW_BASE_URL = import.meta.env.VITE_GEOVIEW_URL || `${window.location.protocol}//${window.location.hostname}:3000`;

const projects = ref([]);
const selectedProjectId = ref(null);
const results = ref([]);
const loading = ref(false);
const error = ref('');

const loadProjects = async () => {
  try {
    const res = await axios.get(`${MINER_API_BASE_URL}/api/projects`);
    projects.value = res.data?.data?.items || [];
  } catch (_) {
    error.value = '项目列表读取失败';
  }
};

const EDITABLE_STATUS = new Set(['ready', 'ready_empty']);

let resultsSeq = 0;

const loadResults = async () => {
  // 竞态门控：快速切换项目时旧响应不得落地（收官审查 P2）
  const seq = (resultsSeq += 1);
  results.value = [];
  if (!selectedProjectId.value) return;
  loading.value = true;
  error.value = '';
  try {
    const res = await axios.get(
      `${MINER_API_BASE_URL}/api/projects/${selectedProjectId.value}/classification-results`,
    );
    if (seq !== resultsSeq) return;
    const items = res.data?.data?.items || res.data?.data || [];
    results.value = (Array.isArray(items) ? items : []).map((item, index) => ({
      key: `${item.result_id ?? index}`,
      resultId: item.result_id ?? item.id,
      fid: item.mine_fid,
      year: item.year,
      featureCount: item.feature_count ?? 0,
      vectorStatus: item.vector_status,
      currentRevisionNo: item.current_revision_no,
      editable: EDITABLE_STATUS.has(item.vector_status),
    }));
  } catch (e) {
    if (seq !== resultsSeq) return;
    error.value = e?.response?.data?.msg || '分类成果清单读取失败';
  } finally {
    if (seq === resultsSeq) loading.value = false;
  }
};

const statusText = (status) => ({
  ready: '矢量就绪',
  ready_empty: '空成果（可人工勾绘）',
  vector_failed: '矢量化失败',
}[status] || status || '状态未知');

const openEditor = (row) => {
  // GeoView 与 Miner 不同源（:3000 vs :4000）：按本机主机名拼 GeoView 基地址
  const target = new URL(`${GEOVIEW_BASE_URL.replace(/\/$/, '')}/classification-results/editor`);
  target.searchParams.set('project_id', String(selectedProjectId.value));
  if (row.resultId) target.searchParams.set('result_id', String(row.resultId));
  window.open(target.toString(), '_blank');
};

loadProjects();
</script>

<style scoped>
.editing-view {
  max-width: 860px;
  margin: 0 auto;
  padding: 28px 24px;
  color: #264b45;
}

.editing-header { margin-bottom: 18px; }
.editing-header h1 { margin: 4px 0 6px; font-size: 22px; }
.workspace-kicker { color: #5a7d75; font-size: 13px; margin: 0; }
.muted-text { color: #5a7d75; font-size: 13px; }

.editing-body {
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid rgba(38, 75, 69, 0.15);
  border-radius: 10px;
  padding: 18px 20px;
}

.editing-row { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }
.editing-row label { font-size: 14px; }
.editing-row select {
  flex: 1; padding: 8px 10px; border-radius: 6px;
  border: 1px solid rgba(38, 75, 69, 0.3);
}

.result-list { display: flex; flex-direction: column; gap: 8px; }
.result-row {
  display: flex; justify-content: space-between; align-items: center; gap: 12px;
  background: rgba(38, 75, 69, 0.04);
  border: 1px solid rgba(38, 75, 69, 0.1);
  border-radius: 8px; padding: 10px 14px;
}
.result-main { display: flex; flex-direction: column; gap: 2px; }

.primary-btn {
  background: #2f6f61; color: #fff; border: none; border-radius: 6px;
  padding: 7px 18px; cursor: pointer; font-size: 13px;
}
.primary-btn:hover { background: #264b45; }
.primary-btn:disabled { opacity: 0.45; cursor: not-allowed; }

.error-text { color: #c0392b; font-size: 13px; }
.empty-block { color: #7ba39a; font-size: 14px; text-align: center; padding: 22px 0; }
</style>
