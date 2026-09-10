<template>
  <section class="export-snapshot-panel">
    <div class="panel-title-row">
      <div>
        <h2>导出与项目配置快照</h2>
        <p class="muted-text">导出和配置快照由服务端写入当前项目的受控目录。</p>
      </div>
      <div class="action-row">
        <button
          v-for="format in exportFormats"
          :key="format"
          class="secondary-btn"
          type="button"
          :disabled="loading || busy || !capabilities?.can_export"
          @click="$emit('create-export', format)"
        >
          导出 {{ format.toUpperCase() }}
        </button>
        <button class="primary-btn" type="button" :disabled="loading || busy" @click="$emit('create-snapshot')">
          生成项目配置快照
        </button>
      </div>
    </div>

    <p v-if="error" class="error-text">{{ error }}</p>

    <section class="sub-panel">
      <div class="section-title-row">
        <h3>导出记录</h3>
        <span>{{ exports.length }} 条</span>
      </div>
      <div class="table-list">
        <article v-for="item in exports" :key="item.id" class="table-row">
          <strong>{{ String(item.format || '').toUpperCase() || '导出成果' }}</strong>
          <span>{{ formatRecordStatus(item.status) }}</span>
          <span>{{ item.artifact_name || '制品处理中' }}</span>
          <small>{{ formatTimestamp(item.create_time) }}</small>
        </article>
        <p v-if="!exports.length" class="empty-block">暂无导出记录。</p>
      </div>
    </section>

    <section class="sub-panel">
      <div class="section-title-row">
        <h3>项目配置快照</h3>
        <span>{{ snapshots.length }} 条</span>
      </div>
      <div class="table-list">
        <article v-for="item in snapshots" :key="item.id" class="table-row snapshot-row">
          <strong>{{ item.snapshot_name || '项目配置快照' }}</strong>
          <span>{{ formatRecordStatus(item.status) }}</span>
          <small>{{ formatTimestamp(item.create_time) }}</small>
          <button
            class="link-btn"
            type="button"
            :disabled="loading || busy || !item.restorable"
            @click="$emit('restore-snapshot', item.id)"
          >恢复配置</button>
        </article>
        <p v-if="!snapshots.length" class="empty-block">暂无项目配置快照。</p>
      </div>
    </section>
  </section>
</template>

<script setup>
defineProps({
  exports: {
    type: Array,
    default: () => [],
  },
  snapshots: {
    type: Array,
    default: () => [],
  },
  capabilities: {
    type: Object,
    default: () => ({}),
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

defineEmits(['create-export', 'create-snapshot', 'restore-snapshot']);

const exportFormats = ['geojson', 'csv', 'shp', 'xlsx'];

function formatRecordStatus(status) {
  return {
    pending: '等待处理',
    processing: '处理中',
    completed: '已完成',
    failed: '失败',
  }[status] || status || '--';
}

function formatTimestamp(value) {
  if (!value) return '时间未记录';
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.getTime()) ? String(value) : timestamp.toLocaleString('zh-CN', { hour12: false });
}
</script>

<style scoped>
.export-snapshot-panel,
.sub-panel,
.table-list {
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

.action-row {
  flex-wrap: wrap;
}

.panel-title-row h2,
.panel-title-row p,
.section-title-row h3,
.error-text,
.empty-block {
  margin: 0;
}

.sub-panel {
  border-top: 1px solid rgba(35, 86, 78, 0.1);
  padding-top: 12px;
}

.table-row {
  display: grid;
  grid-template-columns: minmax(120px, 1fr) auto minmax(150px, 1fr) minmax(150px, 1fr);
  align-items: center;
  gap: 10px;
  border: 1px solid rgba(35, 86, 78, 0.1);
  border-radius: 12px;
  padding: 11px 12px;
  background: #f5f5f7;
}

.snapshot-row {
  grid-template-columns: minmax(140px, 1fr) auto minmax(150px, 1fr) auto;
}

button {
  border-radius: 10px;
  padding: 8px 12px;
  font: inherit;
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

.link-btn {
  border: none;
  background: transparent;
  color: #0a7d5c;
}

.muted-text,
.empty-block,
small {
  color: #6e6e73;
}

.error-text {
  color: #b43c2f;
}

@media (max-width: 840px) {
  .panel-title-row {
    align-items: flex-start;
    flex-direction: column;
  }

  .table-row,
  .snapshot-row {
    grid-template-columns: 1fr;
  }
}
</style>
