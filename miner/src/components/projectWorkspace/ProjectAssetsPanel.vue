<template>
  <section class="assets-panel">
    <div class="panel-title-row">
      <div>
        <h2>项目资产</h2>
        <p class="muted-text">仅展示项目公开资产目录。</p>
      </div>
      <div class="action-row">
        <button class="secondary-btn" type="button" :disabled="loading" @click="$emit('refresh')">
          {{ loading ? '加载中…' : '刷新资产' }}
        </button>
        <button class="primary-btn" type="button" @click="$emit('configure-spatial')">配置空间资源</button>
      </div>
    </div>

    <div class="filter-row" aria-label="资产筛选">
      <label>
        类型
        <select :value="filters?.type || ''" @change="updateFilter('type', $event.target.value)">
          <option value="">全部类型</option>
          <option v-for="type in assetTypes" :key="type" :value="type">{{ formatAssetType(type) }}</option>
        </select>
      </label>
      <label>
        状态
        <select :value="filters?.status || ''" @change="updateFilter('status', $event.target.value)">
          <option value="">全部状态</option>
          <option v-for="status in assetStatuses" :key="status" :value="status">{{ formatAssetStatus(status) }}</option>
        </select>
      </label>
    </div>

    <p v-if="error" class="error-text">{{ error }}</p>
    <p v-else-if="loading && !items.length" class="empty-block">资产目录加载中…</p>
    <div v-else class="asset-list">
      <article v-for="asset in items" :key="asset.id" class="asset-row">
        <div>
          <strong>{{ asset.name || '未命名资产' }}</strong>
          <p class="muted-text">{{ formatAssetType(asset.asset_type) }} · {{ asset.format || '格式未知' }}</p>
        </div>
        <span class="status-pill" :data-status="asset.status">{{ formatAssetStatus(asset.status) }}</span>
        <span>版本 {{ asset.version ?? '--' }}</span>
        <span>{{ formatTemporal(asset.temporal) }}</span>
        <p v-if="asset.error?.message" class="asset-error">{{ asset.error.message }}</p>
      </article>
      <p v-if="!items.length" class="empty-block">暂无符合条件的公开资产。</p>
    </div>
  </section>
</template>

<script setup>
const props = defineProps({
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
  filters: {
    type: Object,
    default: () => ({}),
  },
});

const emit = defineEmits(['update:filters', 'refresh', 'configure-spatial']);

const assetTypes = [
  'mine_boundary',
  'basemap',
  'imagery',
  'inference_result',
  'vector_revision',
  'report',
  'export',
  'backup_snapshot',
];
const assetStatuses = ['registered', 'processing', 'ready', 'failed', 'superseded'];

function updateFilter(key, value) {
  emit('update:filters', { ...props.filters, [key]: value });
}

function formatAssetType(type) {
  return {
    mine_boundary: '矿山边界',
    basemap: '底图',
    imagery: '影像',
    inference_result: '推理成果',
    vector_revision: '矢量修订',
    report: '报告',
    export: '导出成果',
    backup_snapshot: '项目配置快照',
  }[type] || type || '--';
}

function formatAssetStatus(status) {
  return {
    registered: '已登记',
    processing: '处理中',
    ready: '已就绪',
    failed: '失败',
    superseded: '已替代',
  }[status] || status || '--';
}

function formatTemporal(temporal) {
  if (!temporal?.start && !temporal?.end) return '时间未标注';
  return `${temporal.start || '--'} - ${temporal.end || '--'}`;
}
</script>

<style scoped>
.assets-panel,
.asset-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.panel-title-row,
.action-row,
.filter-row {
  display: flex;
  gap: 10px;
}

.panel-title-row {
  justify-content: space-between;
  align-items: flex-start;
}

.action-row {
  flex-wrap: wrap;
  align-items: center;
}

.panel-title-row h2,
.panel-title-row p,
.asset-row p,
.error-text,
.empty-block {
  margin: 0;
}

.filter-row {
  flex-wrap: wrap;
}

label {
  display: flex;
  align-items: center;
  gap: 8px;
}

select,
button {
  font: inherit;
}

select {
  border: 1px solid rgba(35, 86, 78, 0.18);
  border-radius: 10px;
  padding: 8px 10px;
  background: #fff;
}

.asset-row {
  display: grid;
  grid-template-columns: minmax(180px, 1.6fr) auto auto minmax(130px, 1fr);
  align-items: center;
  gap: 10px;
  border: 1px solid rgba(35, 86, 78, 0.1);
  border-radius: 12px;
  padding: 12px;
  background: #f5f5f7;
}

.asset-error {
  grid-column: 1 / -1;
  color: #b43c2f;
}

.muted-text,
.empty-block {
  color: #6e6e73;
}

.status-pill {
  border-radius: 999px;
  padding: 3px 8px;
  background: #f5f5f7;
  color: #086a4f;
  font-size: 12px;
}

.status-pill[data-status='failed'] {
  background: #fff0ee;
  color: #b43c2f;
}

.primary-btn,
.secondary-btn {
  border-radius: 10px;
  padding: 8px 12px;
  cursor: pointer;
}

.primary-btn {
  border: none;
  background: #0a7d5c;
  color: #fff;
}

.secondary-btn {
  border: 1px solid rgba(47, 122, 104, 0.24);
  background: rgba(10, 125, 92, 0.08);
  color: #086a4f;
}

.error-text {
  color: #b43c2f;
}

@media (max-width: 840px) {
  .panel-title-row,
  .asset-row {
    grid-template-columns: 1fr;
    flex-direction: column;
  }

  .asset-row {
    align-items: start;
  }
}
</style>
