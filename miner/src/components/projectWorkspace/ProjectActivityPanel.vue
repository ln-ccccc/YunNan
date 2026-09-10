<template>
  <section class="activity-panel">
    <div class="panel-title-row">
      <div>
        <h2>项目活动</h2>
        <p class="muted-text">活动以稳定 action_code 记录，界面仅负责本地化展示。</p>
      </div>
      <button class="secondary-btn" type="button" :disabled="loading" @click="$emit('refresh')">
        {{ loading ? '加载中…' : '刷新活动' }}
      </button>
    </div>

    <p v-if="error" class="error-text">{{ error }}</p>
    <p v-else-if="loading && !items.length" class="empty-block">活动加载中…</p>
    <ol v-else class="activity-list">
      <li v-for="item in items" :key="activityKey(item)" class="activity-item">
        <strong>{{ formatActivityAction(item.action_code) }}</strong>
        <span v-if="item.result" class="result-pill">{{ formatResult(item.result) }}</span>
        <span class="muted-text">{{ item.actor || 'system' }}</span>
        <small>{{ formatTimestamp(item.created_at) }}</small>
      </li>
      <li v-if="!items.length" class="empty-block">暂无项目活动。</li>
    </ol>
  </section>
</template>

<script setup>
import { formatActivityAction } from '../../projectWorkspace/projectWorkspaceViewModel.js';

defineProps({
  items: {
    type: Array,
    default: () => [],
  },
  loading: {
    type: Boolean,
    default: false,
  },
  error: {
    type: String,
    default: '',
  },
});

defineEmits(['refresh']);

function activityKey(item) {
  return item.id || [item.action_code, item.target?.type, item.target?.id, item.created_at].join(':');
}

function formatResult(result) {
  return {
    success: '成功',
    succeeded: '成功',
    failed: '失败',
    queued: '已提交',
    cancelled: '已取消',
  }[result] || result;
}

function formatTimestamp(value) {
  if (!value) return '时间未记录';
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.getTime()) ? String(value) : timestamp.toLocaleString('zh-CN', { hour12: false });
}
</script>

<style scoped>
.activity-panel,
.activity-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.panel-title-row,
.activity-item {
  display: flex;
  align-items: center;
  gap: 10px;
}

.panel-title-row {
  justify-content: space-between;
}

.panel-title-row h2,
.panel-title-row p,
.error-text,
.empty-block {
  margin: 0;
}

.activity-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.activity-item {
  justify-content: space-between;
  border: 1px solid rgba(0, 0, 0, 0.07);
  border-radius: 12px;
  padding: 11px 12px;
  background: rgba(0, 0, 0, 0.03);
}

.activity-item strong {
  flex: 1;
}

.result-pill {
  border-radius: 999px;
  padding: 3px 8px;
  background: #f5f5f7;
  color: #086a4f;
  font-size: 12px;
}

.secondary-btn {
  border: 1px solid rgba(47, 122, 104, 0.24);
  border-radius: 10px;
  padding: 8px 12px;
  background: rgba(10, 125, 92, 0.08);
  color: #086a4f;
  font: inherit;
  cursor: pointer;
}

.muted-text,
.empty-block,
small {
  color: #6e6e73;
}

.error-text {
  color: #b43c2f;
}

@media (max-width: 620px) {
  .panel-title-row,
  .activity-item {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
