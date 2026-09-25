<template>
  <div class="interpretation-view">
    <header class="view-header">
      <div>
        <p class="kicker">智能解译</p>
        <h1>推理记录与成果</h1>
        <p class="muted-text">项目维度的解译历史浏览；发起解译经地图工作台，修订成果经图斑编辑。</p>
      </div>
    </header>

    <section class="view-body">
      <div class="toolbar-row">
        <label for="interpretation-project">项目</label>
        <select id="interpretation-project" v-model="selectedProjectId" @change="resetAndLoad">
          <option :value="null" disabled>请选择项目</option>
          <option v-for="project in projects" :key="project.id" :value="project.id">
            {{ project.name }}（{{ project.status }}）
          </option>
        </select>
        <button class="primary-btn" type="button" :disabled="!selectedProjectId" @click="$emit('go-map')">
          发起新解译
        </button>
      </div>
      <p class="hint-text">发起新解译将跳转到地图工作台，选择矿山后按 KML ROI 推理。</p>

      <p v-if="loading" class="empty-block">解译记录加载中…</p>
      <p v-else-if="error" class="error-text">{{ error }}</p>
      <p v-else-if="!selectedProjectId" class="empty-block">先选择项目</p>
      <p v-else-if="!records.length" class="empty-block">该项目暂无解译记录（可在地图工作台发起）</p>
      <template v-else>
        <div v-for="row in records" :key="row.record_id" class="record-row">
          <div class="thumb-group">
            <img class="thumb" :src="row.before_img" alt="原图" loading="lazy" />
            <img class="thumb" :src="row.after_img" alt="预测结果" loading="lazy" />
          </div>
          <div class="record-main">
            <strong>矿山 {{ row.data?.fid ?? '?' }} · {{ row.data?.year ?? '未知年份' }}</strong>
            <span class="muted-text">
              {{ row.type }} · {{ row.data?.file || '未知产物' }}
            </span>
          </div>
          <span class="record-mode">{{ row.data?.mode === 'project' ? '项目推理' : '快速推理' }}</span>
        </div>

        <div class="pager-row">
          <button type="button" :disabled="page <= 1" @click="changePage(page - 1)">上一页</button>
          <span class="muted-text">第 {{ page }} 页 / 共 {{ totalPages }} 页（{{ total }} 条）</span>
          <button type="button" :disabled="page >= totalPages" @click="changePage(page + 1)">下一页</button>
        </div>
      </template>
    </section>
  </div>
</template>

<script setup>
import axios from 'axios';
import { computed, ref } from 'vue';

defineEmits(['go-map']);

const MINER_API_BASE_URL = import.meta.env.VITE_MINER_API_BASE_URL || '';

const projects = ref([]);
const selectedProjectId = ref(null);
const records = ref([]);
const page = ref(1);
const limit = 20;
const total = ref(0);
const loading = ref(false);
const error = ref('');

let requestSeq = 0;

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / limit)));

const loadProjects = async () => {
  try {
    const res = await axios.get(`${MINER_API_BASE_URL}/api/projects`);
    projects.value = res.data?.data?.items || [];
  } catch (_) {
    error.value = '项目列表读取失败';
  }
};

const loadHistory = async () => {
  // 竞态门控：快速切换项目/翻页时旧响应不得落地（同 EditingView 惯例）
  const seq = (requestSeq += 1);
  if (!selectedProjectId.value) return;
  loading.value = true;
  error.value = '';
  try {
    const res = await axios.get(`${MINER_API_BASE_URL}/api/interpretation/kml-roi-history`, {
      params: { project_id: selectedProjectId.value, page: page.value, limit },
    });
    if (seq !== requestSeq) return;
    records.value = res.data?.data || [];
    total.value = res.data?.count ?? 0;
  } catch (e) {
    if (seq !== requestSeq) return;
    error.value = e?.response?.data?.msg || '解译记录读取失败';
  } finally {
    if (seq === requestSeq) loading.value = false;
  }
};

const resetAndLoad = () => {
  page.value = 1;
  loadHistory();
};

const changePage = (next) => {
  page.value = next;
  loadHistory();
};

loadProjects();
</script>

<style scoped>
.interpretation-view {
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

.toolbar-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.toolbar-row label { font-size: 14px; }
.toolbar-row select {
  padding: 7px 10px; border-radius: 6px;
  border: 1px solid rgba(38, 75, 69, 0.3); font-size: 13px;
}
.hint-text { margin: 10px 0 0; font-size: 12px; color: #5a7d75; }

.primary-btn {
  background: #2f6f61; color: #fff; border: none; border-radius: 6px;
  padding: 8px 18px; cursor: pointer; font-size: 13px;
}
.primary-btn:disabled { cursor: not-allowed; opacity: 0.55; }

.empty-block { margin: 18px 0; text-align: center; color: #5a7d75; font-size: 13px; }
.error-text { margin: 12px 0 0; color: #b43c2f; font-size: 13px; }

.record-row {
  display: flex; align-items: center; gap: 14px;
  padding: 10px 4px; border-bottom: 1px solid rgba(38, 75, 69, 0.08);
}
.thumb-group { display: flex; gap: 6px; }
.thumb {
  width: 72px; height: 54px; object-fit: cover; border-radius: 4px;
  border: 1px solid rgba(38, 75, 69, 0.15); background: rgba(38, 75, 69, 0.04);
}
.record-main { flex: 1; display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.record-main strong { font-size: 14px; }
.record-mode { font-size: 12px; color: #5a7d75; }

.pager-row {
  display: flex; align-items: center; justify-content: center; gap: 14px;
  margin-top: 14px; font-size: 12px;
}
.pager-row button {
  padding: 5px 14px; border-radius: 6px; cursor: pointer; font-size: 12px;
  border: 1px solid rgba(38, 75, 69, 0.3); background: #fff; color: #264b45;
}
.pager-row button:disabled { cursor: not-allowed; opacity: 0.5; }
</style>
