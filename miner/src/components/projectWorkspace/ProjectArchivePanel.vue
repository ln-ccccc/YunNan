<template>
  <section class="archive-panel">
    <div class="panel-title-row">
      <h2>项目档案</h2>
      <span class="muted-text">一项目一档案：资产台账 · 配置快照 · 活动时间线</span>
    </div>
    <p v-if="error" class="error-text">{{ error }}</p>
    <p v-else-if="loading" class="empty-block">档案加载中…</p>
    <template v-else>
      <div class="archive-grid">
        <article class="archive-card">
          <h3>资产台账（{{ assetCount }} 项）</h3>
          <ul class="asset-type-list">
            <li v-for="row in assetTypeRows" :key="row.type">
              <span>{{ row.label }}</span>
              <strong>{{ row.count }}</strong>
            </li>
            <li v-if="!assetTypeRows.length" class="muted-text">暂无资产</li>
          </ul>
        </article>
        <article class="archive-card">
          <h3>配置快照（{{ backups.length }} 份）</h3>
          <div class="import-row">
            <input
              ref="importInputRef"
              type="file"
              accept=".json,application/json"
              style="display: none;"
              @change="handleImportFile"
            >
            <button class="secondary-btn" type="button" :disabled="importing" @click="importInputRef?.click()">
              {{ importing ? '导入中…' : '导入快照（manifest.json）' }}
            </button>
          </div>
          <ul class="backup-list">
            <li v-for="backup in backups.slice(0, 5)" :key="backup.id">
              <span>#{{ backup.id }} · {{ backup.scope }}</span>
              <span>{{ formatTime(backup.create_time) }}</span>
            </li>
            <li v-if="!backups.length" class="muted-text">暂无快照</li>
          </ul>
        </article>
        <article class="archive-card">
          <h3>最近活动</h3>
          <ul class="activity-list">
            <li v-for="activity in activities.slice(0, 8)" :key="activity.id || activity.created_at">
              <span>{{ formatEvent(activity.event_type) }}</span>
              <span class="muted-text">{{ formatTime(activity.created_at) }}</span>
            </li>
            <li v-if="!activities.length" class="muted-text">暂无活动</li>
          </ul>
        </article>
      </div>
    </template>
  </section>
</template>

<script setup>
import axios from 'axios';
import { computed, onMounted, ref } from 'vue';

const props = defineProps({
  projectId: { type: Number, required: true },
});
const emit = defineEmits(['imported']);

const MINER_API_BASE_URL = import.meta.env.VITE_MINER_API_BASE_URL || '';

const assets = ref([]);
const backups = ref([]);
const activities = ref([]);
const loading = ref(true);
const error = ref('');
const importing = ref(false);
const importInputRef = ref(null);

const ASSET_TYPE_LABELS = {
  mine_boundary: '矿山边界',
  basemap: '底图',
  imagery: '影像',
  inference_result: '推理成果',
  vector_revision: '矢量修订',
  report: '报告',
  export: '导出',
  backup_snapshot: '配置快照',
};

const assetTypeRows = computed(() => {
  const counts = {};
  for (const asset of assets.value) {
    const type = asset.asset_type || asset.type || 'other';
    counts[type] = (counts[type] || 0) + 1;
  }
  return Object.entries(counts)
    .map(([type, count]) => ({ type, count, label: ASSET_TYPE_LABELS[type] || type }));
});

const assetCount = computed(() => assets.value.length);

const EVENT_LABELS = {
  project_created: '创建项目',
  project_updated: '更新项目',
  project_archived: '归档项目',
  project_restored: '恢复项目',
  project_deleted: '删除项目',
  project_imported: '导入快照',
  backup_created: '创建快照',
  backup_restored: '恢复快照',
  inference_started: '发起推理',
};

const formatEvent = (event) => EVENT_LABELS[event] || event || '未知事件';
const formatTime = (value) => {
  if (!value) return '时间未知';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', { hour12: false });
};

const fetchArchive = async () => {
  loading.value = true;
  error.value = '';
  const base = `${MINER_API_BASE_URL}/api/projects/${props.projectId}`;
  const unwrap = (res) => res.data?.data;
  try {
    const [assetRes, backupRes, timelineRes] = await Promise.all([
      axios.get(`${base}/assets`).catch(() => null),
      axios.get(`${base}/backups`).catch(() => null),
      axios.get(`${base}/timeline`).catch(() => null),
    ]);
    assets.value = unwrap(assetRes?.data ? assetRes : { data: { data: { items: [] } } })?.items || [];
    // assets 响应形状兼容：{items:[...]} 或直接数组
    if (Array.isArray(assets.value)) assets.value = assets.value || [];
    const assetPayload = assetRes?.data?.data;
    assets.value = Array.isArray(assetPayload) ? assetPayload : (assetPayload?.items || []);
    const backupPayload = backupRes?.data?.data;
    backups.value = Array.isArray(backupPayload) ? backupPayload : (backupPayload?.items || []);
    activities.value = timelineRes?.data?.data?.items || [];
  } catch (e) {
    error.value = '项目档案读取失败';
  } finally {
    loading.value = false;
  }
};

const handleImportFile = async (event) => {
  const file = event?.target?.files?.[0];
  if (!file) return;
  importing.value = true;
  try {
    const text = await file.text();
    let manifest;
    try {
      manifest = JSON.parse(text);
    } catch (_) {
      window.alert('文件不是合法的 JSON（应为导出的 manifest.json）');
      return;
    }
    await axios.post(
      `${MINER_API_BASE_URL}/api/projects/${props.projectId}/backups/import`,
      { manifest },
    );
    window.alert('快照导入成功：项目基础信息与矿山/数据集清单已应用');
    emit('imported');
    await fetchArchive();
  } catch (e) {
    const msg = e?.response?.data?.msg || '快照导入失败，请检查文件格式';
    window.alert(msg);
  } finally {
    importing.value = false;
    if (event?.target) event.target.value = '';
  }
};

defineExpose({ fetchArchive });

onMounted(fetchArchive);
</script>

<style scoped>
.archive-panel {
  background: #fff;
  border: 1px solid rgba(38, 75, 69, 0.15);
  border-radius: 10px;
  padding: 16px 18px;
}

.panel-title-row { display: flex; align-items: baseline; gap: 12px; margin-bottom: 12px; }
.panel-title-row h2 { margin: 0; font-size: 17px; color: #264b45; }
.muted-text { color: #7ba39a; font-size: 12px; }

.archive-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 12px;
}

.archive-card {
  background: rgba(38, 75, 69, 0.04);
  border: 1px solid rgba(38, 75, 69, 0.1);
  border-radius: 8px;
  padding: 12px;
}

.archive-card h3 { margin: 0 0 8px; font-size: 14px; color: #264b45; }

.asset-type-list, .backup-list, .activity-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
}

.asset-type-list li, .backup-list li, .activity-list li {
  display: flex;
  justify-content: space-between;
  gap: 8px;
}

.import-row { margin-bottom: 8px; }

.secondary-btn {
  background: #fff;
  border: 1px solid #2f6f61;
  color: #2f6f61;
  border-radius: 6px;
  padding: 6px 14px;
  cursor: pointer;
  font-size: 13px;
}
.secondary-btn:hover { background: rgba(47, 111, 97, 0.08); }
.secondary-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.error-text { color: #c0392b; font-size: 13px; }
.empty-block { color: #7ba39a; font-size: 13px; padding: 16px 0; text-align: center; }
</style>
