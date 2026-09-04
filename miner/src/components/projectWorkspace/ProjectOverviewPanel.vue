<template>
  <section class="overview-panel">
    <p v-if="error" class="error-text">{{ error }}</p>
    <p v-else-if="loading && !overview" class="empty-block">项目概览加载中…</p>
    <template v-else-if="overview">
      <div class="panel-title-row">
        <div>
          <h2>{{ overview.summary?.name || '未命名项目' }}</h2>
          <p class="muted-text">
            {{ overview.summary?.region || '未填写区域' }}
            · {{ formatYearRange(overview.summary?.monitor_start_year, overview.summary?.monitor_end_year) }}
          </p>
        </div>
        <div class="action-row">
          <button class="secondary-btn" type="button" :disabled="busy" @click="$emit('edit')">编辑项目</button>
          <button class="secondary-btn" type="button" :disabled="loading" @click="$emit('refresh')">刷新详情</button>
          <button
            class="secondary-btn"
            type="button"
            :disabled="busy || !overview.capabilities?.can_open_map"
            @click="$emit('open-map')"
          >
            打开矿山地图
          </button>
          <button
            v-if="overview.capabilities?.can_start_inference"
            class="secondary-btn"
            type="button"
            :disabled="busy"
            @click="$emit('start-inference')"
          >
            开始地物分类
          </button>
          <button
            v-if="overview.lifecycle_status !== 'archived'"
            class="ghost-btn"
            type="button"
            :disabled="busy"
            @click="$emit('archive')"
          >归档</button>
          <button v-else class="ghost-btn" type="button" :disabled="busy" @click="$emit('restore')">恢复</button>
        </div>
      </div>

      <div class="summary-grid">
        <article class="summary-card">
          <span>生命周期</span>
          <strong>{{ formatLifecycleStatus(overview.lifecycle_status) }}</strong>
        </article>
        <article class="summary-card">
          <span>工作流准备度</span>
          <strong>{{ formatReadiness(overview.readiness?.status) }}</strong>
          <small>{{ overview.readiness?.passed || 0 }} / {{ overview.readiness?.total || 0 }} 项已满足</small>
        </article>
        <article class="summary-card">
          <span>负责人</span>
          <strong>{{ overview.summary?.manager || '未填写' }}</strong>
        </article>
        <article class="summary-card">
          <span>资产数量</span>
          <strong>{{ overview.counts?.assets || 0 }}</strong>
          <small>失败 {{ overview.counts?.failed_assets || 0 }} 项</small>
        </article>
      </div>

      <p class="muted-text">{{ overview.summary?.remark || '暂无备注' }}</p>

      <section class="detail-section">
        <div class="section-title-row">
          <h3>工作流检查</h3>
          <span>{{ overview.readiness?.passed || 0 }} / {{ overview.readiness?.total || 0 }}</span>
        </div>
        <ul class="check-list">
          <li v-for="check in overview.readiness?.checks || []" :key="check.code">
            <span>{{ formatCheckLabel(check.code) }}</span>
            <span :class="check.status === 'passed' ? 'success-text' : 'warning-text'">
              {{ check.status === 'passed' ? '已满足' : formatBlocker(check.reason_code) }}
            </span>
          </li>
        </ul>
      </section>

      <section v-if="overview.next_actions?.length" class="detail-section">
        <h3>下一步</h3>
        <div class="action-row">
          <button
            v-for="action in overview.next_actions"
            :key="`${action.action_code}:${action.target || ''}`"
            class="secondary-btn"
            type="button"
            @click="$emit('run-action', action)"
          >
            {{ formatActionLabel(action.action_code) }}
          </button>
        </div>
      </section>
    </template>
    <p v-else class="empty-block">选择项目后查看项目概览。</p>
  </section>
</template>

<script setup>
import { formatActionLabel } from '../../projectWorkspace/projectWorkspaceViewModel.js';

defineProps({
  overview: {
    type: Object,
    default: null,
  },
  loading: {
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
});

defineEmits(['edit', 'refresh', 'archive', 'restore', 'open-map', 'start-inference', 'run-action']);

function formatLifecycleStatus(status) {
  return {
    draft: '草稿',
    active: '进行中',
    completed: '已完成',
    archived: '已归档',
  }[status] || status || '--';
}

function formatReadiness(status) {
  return {
    blocked: '受阻',
    partial: '部分就绪',
    ready: '已就绪',
  }[status] || status || '--';
}

function formatCheckLabel(code) {
  return {
    PROJECT_PROFILE: '项目建档',
    MINE_BOUNDARY: '矿山边界',
    ACTIVE_BASEMAP: '激活底图',
    INFERENCE_INPUT: '推理影像',
    REVIEWABLE_RESULT: '可审阅成果',
  }[code] || code || '--';
}

function formatBlocker(code) {
  return {
    PROJECT_PROFILE_INCOMPLETE: '项目基本信息不完整',
    NO_MINE_BOUNDARY: '缺少矿山边界',
    NO_ACTIVE_BASEMAP: '缺少激活底图',
    NO_INFERENCE_INPUT: '缺少推理影像',
    NO_REVIEWABLE_RESULT: '缺少可审阅成果',
  }[code] || code || '待处理';
}

function formatYearRange(startYear, endYear) {
  if (!startYear && !endYear) return '监测期未设置';
  return `${startYear || '--'} - ${endYear || '--'}`;
}
</script>

<style scoped>
.overview-panel,
.detail-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.panel-title-row,
.section-title-row,
.action-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.panel-title-row,
.section-title-row {
  justify-content: space-between;
}

.panel-title-row h2,
.section-title-row h3,
.detail-section h3,
.muted-text,
.error-text,
.empty-block {
  margin: 0;
}

.action-row {
  flex-wrap: wrap;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.summary-card {
  display: flex;
  flex-direction: column;
  gap: 5px;
  border: 1px solid rgba(35, 86, 78, 0.1);
  border-radius: 12px;
  padding: 12px;
  background: #f4faf7;
}

.summary-card small,
.muted-text,
.empty-block {
  color: #5d6f6d;
}

.check-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.check-list li {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  border-radius: 10px;
  padding: 10px;
  background: #f8fcfa;
}

button {
  font: inherit;
  border-radius: 10px;
  padding: 8px 12px;
  cursor: pointer;
}

.secondary-btn {
  border: 1px solid rgba(47, 122, 104, 0.24);
  background: #eaf6f1;
  color: #1f5c4d;
}

.ghost-btn {
  border: none;
  background: transparent;
  color: #2f7a68;
}

.success-text {
  color: #237a59;
}

.warning-text,
.error-text {
  color: #b43c2f;
}

@media (max-width: 900px) {
  .panel-title-row {
    align-items: flex-start;
    flex-direction: column;
  }

  .summary-grid,
  .check-list {
    grid-template-columns: 1fr;
  }
}
</style>
