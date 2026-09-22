<template>
  <div class="imagery-view">
    <header class="view-header">
      <div>
        <p class="kicker">影像管理</p>
        <h1>空间资源与影像</h1>
        <p class="muted-text">矿山边界导入、底图注册与推理影像登记均在工作台内完成，此处提供跨项目入口。</p>
      </div>
    </header>

    <section class="view-body">
      <div class="row">
        <label for="imagery-project">项目</label>
        <select id="imagery-project" v-model="selectedProjectId">
          <option :value="null" disabled>请选择项目</option>
          <option v-for="project in projects" :key="project.id" :value="project.id">
            {{ project.name }}
          </option>
        </select>
        <button class="primary-btn" type="button" :disabled="!selectedProjectId" @click="goWorkspace">
          打开项目工作台（影像登记 / 空间资源 / KML 上传）
        </button>
      </div>

      <div v-if="selectedProjectId" class="summary-cards">
        <article class="card">
          <span>矿山绑定</span>
          <strong>{{ currentSummary?.mine_count ?? '—' }}</strong>
        </article>
        <article class="card">
          <span>数据集</span>
          <strong>{{ currentSummary?.dataset_count ?? '—' }}</strong>
        </article>
        <article class="card">
          <span>图斑</span>
          <strong>{{ currentSummary?.feature_count ?? '—' }}</strong>
        </article>
        <article class="card">
          <span>地图状态</span>
          <strong>{{ currentSummary?.map_ready ? '就绪' : '未就绪' }}</strong>
        </article>
      </div>
      <p v-else class="empty-block">选择项目后可查看资源摘要并进入工作台操作</p>
    </section>
  </div>
</template>

<script setup>
import axios from 'axios';
import { computed, ref } from 'vue';

const emit = defineEmits(['go-map']);

const MINER_API_BASE_URL = import.meta.env.VITE_MINER_API_BASE_URL || '';

const projects = ref([]);
const selectedProjectId = ref(null);
const error = ref('');

const loadProjects = async () => {
  try {
    const res = await axios.get(`${MINER_API_BASE_URL}/api/projects`);
    projects.value = res.data?.data?.items || [];
  } catch (_) {
    error.value = '项目列表读取失败';
  }
};

const currentSummary = computed(() =>
  projects.value.find((p) => p.id === selectedProjectId.value) || null
);

const goWorkspace = () => {
  if (selectedProjectId.value) emit('go-map', selectedProjectId.value);
};

loadProjects();
</script>

<style scoped>
.imagery-view { max-width: 860px; margin: 0 auto; padding: 28px 24px; color: #264b45; }
.view-header { margin-bottom: 18px; }
.view-header h1 { margin: 4px 0 6px; font-size: 22px; }
.kicker { color: #5a7d75; font-size: 13px; margin: 0; }
.muted-text { color: #5a7d75; font-size: 13px; }

.view-body {
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid rgba(38, 75, 69, 0.15);
  border-radius: 10px;
  padding: 18px 20px;
}

.row { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }
.row label { font-size: 14px; }
.row select {
  flex: 1; min-width: 200px; padding: 8px 10px; border-radius: 6px;
  border: 1px solid rgba(38, 75, 69, 0.3);
}

.primary-btn {
  background: #2f6f61; color: #fff; border: none; border-radius: 6px;
  padding: 8px 16px; cursor: pointer; font-size: 13px;
}
.primary-btn:hover { background: #264b45; }
.primary-btn:disabled { opacity: 0.45; cursor: not-allowed; }

.summary-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; }
.card {
  background: rgba(38, 75, 69, 0.05); border: 1px solid rgba(38, 75, 69, 0.12);
  border-radius: 8px; padding: 10px 14px; display: flex; flex-direction: column; gap: 4px;
}
.card span { font-size: 12px; color: #5a7d75; }
.card strong { font-size: 20px; }

.empty-block { color: #7ba39a; font-size: 14px; text-align: center; padding: 22px 0; }
</style>
