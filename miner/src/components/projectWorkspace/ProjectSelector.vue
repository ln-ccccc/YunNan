<template>
  <section class="project-selector">
    <div class="filter-grid" aria-label="项目筛选">
      <label>
        项目名称
        <input :value="filters?.name || ''" placeholder="项目名称" @input="updateFilter('name', $event.target.value)" />
      </label>
      <label>
        区域
        <input :value="filters?.region || ''" placeholder="区域" @input="updateFilter('region', $event.target.value)" />
      </label>
      <label>
        监测年份
        <input :value="filters?.monitorYear || ''" inputmode="numeric" placeholder="监测年份" @input="updateFilter('monitorYear', $event.target.value)" />
      </label>
      <label>
        项目状态
        <select :value="filters?.status || ''" @change="updateFilter('status', $event.target.value)">
          <option value="">全部状态</option>
          <option v-for="status in lifecycleStatuses" :key="status" :value="status">
            {{ formatLifecycleStatus(status) }}
          </option>
        </select>
      </label>
    </div>

    <div class="action-row">
      <button class="secondary-btn" type="button" @click="$emit('reset')">重置筛选</button>
      <button class="secondary-btn" type="button" :disabled="loading" @click="$emit('refresh')">
        {{ loading ? '加载中…' : '刷新列表' }}
      </button>
      <button class="primary-btn" type="button" @click="$emit('create')">新建项目</button>
    </div>

    <div class="panel-title-row">
      <h2>项目列表</h2>
      <span>{{ loading ? '加载中…' : `${visibleItems.length} 个项目` }}</span>
    </div>
    <p v-if="error" class="error-text">{{ error }}</p>

    <div class="project-card-list">
      <p v-if="loading && !visibleItems.length" class="empty-block">项目列表加载中…</p>
      <template v-else>
        <button
          v-for="item in visibleItems"
          :key="item.id"
          class="project-card"
          :class="{ active: isSelected(item.id) }"
          :aria-pressed="isSelected(item.id)"
          type="button"
          @click="$emit('select', item.id)"
        >
          <span class="project-card-top">
            <strong>{{ item.name || '未命名项目' }}</strong>
            <span class="status-pill">{{ formatLifecycleStatus(item.lifecycle_status || item.status) }}</span>
          </span>
          <span>{{ item.region || '未填写区域' }}</span>
          <span>监测期：{{ formatYearRange(item.monitor_start_year, item.monitor_end_year) }}</span>
          <span>矿山 {{ item.mine_count || 0 }} 座，数据 {{ item.dataset_count || 0 }} 份</span>
        </button>
        <p v-if="!visibleItems.length" class="empty-block">暂无匹配项目</p>
      </template>
    </div>
  </section>
</template>

<script setup>
import { computed } from 'vue';

import { filterProjects } from '../../projectWorkspace/projectWorkspaceHelpers.js';

const props = defineProps({
  items: {
    type: Array,
    default: () => [],
  },
  selectedId: {
    type: [Number, String],
    default: null,
  },
  filters: {
    type: Object,
    default: () => ({}),
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

const emit = defineEmits(['select', 'update:filters', 'reset', 'refresh', 'create']);

const lifecycleStatuses = ['draft', 'active', 'completed', 'archived'];
const visibleItems = computed(() => filterProjects(props.items, props.filters));

function updateFilter(key, value) {
  emit('update:filters', { ...props.filters, [key]: value });
}

function isSelected(projectId) {
  return String(projectId) === String(props.selectedId);
}

function formatLifecycleStatus(status) {
  return {
    draft: '草稿',
    active: '进行中',
    completed: '已完成',
    archived: '已归档',
  }[status] || status || '--';
}

function formatYearRange(startYear, endYear) {
  if (!startYear && !endYear) return '未设置';
  return `${startYear || '--'} - ${endYear || '--'}`;
}
</script>

<style scoped>
.project-selector,
.project-card-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.filter-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

label,
.project-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

input,
select,
button {
  font: inherit;
}

input,
select {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid rgba(35, 86, 78, 0.18);
  border-radius: 10px;
  padding: 8px 10px;
}

.action-row,
.panel-title-row,
.project-card-top {
  display: flex;
  align-items: center;
  gap: 8px;
}

.action-row {
  flex-wrap: wrap;
}

.panel-title-row,
.project-card-top {
  justify-content: space-between;
}

.panel-title-row h2,
.panel-title-row span {
  margin: 0;
}

.project-card {
  border: 1px solid rgba(35, 86, 78, 0.15);
  border-radius: 12px;
  padding: 12px;
  background: #f8fcfa;
  color: inherit;
  text-align: left;
  cursor: pointer;
}

.project-card.active {
  border-color: #2f7a68;
  background: #edf7f3;
}

.status-pill {
  border-radius: 999px;
  padding: 3px 8px;
  background: #edf7f3;
  color: #1f5c4d;
  font-size: 12px;
}

.primary-btn,
.secondary-btn {
  border-radius: 10px;
  padding: 8px 12px;
  cursor: pointer;
}

.primary-btn {
  border: none;
  background: #2f7a68;
  color: #fff;
}

.secondary-btn {
  border: 1px solid rgba(47, 122, 104, 0.24);
  background: #eaf6f1;
  color: #1f5c4d;
}

.error-text {
  margin: 0;
  color: #b43c2f;
}

.empty-block {
  margin: 0;
  color: #5d6f6d;
}

@media (max-width: 720px) {
  .filter-grid {
    grid-template-columns: 1fr;
  }
}
</style>
