<template>
  <div class="stats-panel">
    <div v-if="loading" class="stats-loading">统计加载中...</div>
    <div v-else-if="error" class="stats-error">{{ error }}</div>
    <template v-else>
      <div class="stat-cards">
        <div class="stat-card">
          <span class="stat-label">项目总数</span>
          <span class="stat-value">{{ overview.project_total }}</span>
          <span class="stat-sub">在建 {{ overview.project_counts?.active || 0 }} · 草稿 {{ overview.project_counts?.draft || 0 }} · 完成 {{ overview.project_counts?.completed || 0 }}</span>
        </div>
        <div class="stat-card">
          <span class="stat-label">矿山总数</span>
          <span class="stat-value">{{ overview.mine_total }}</span>
        </div>
        <div class="stat-card">
          <span class="stat-label">图斑总数</span>
          <span class="stat-value">{{ overview.feature_total }}</span>
        </div>
        <div class="stat-card" :class="{ alert: (overview.suspect_mine_count || 0) > 0 }">
          <span class="stat-label">新增修复面积</span>
          <span class="stat-value">{{ formatAreaKm2(overview.restored_area_m2) }}</span>
        </div>
        <div class="stat-card" :class="{ alert: (overview.suspect_mine_count || 0) > 0 }">
          <span class="stat-label">疑似异常图斑</span>
          <span class="stat-value">{{ overview.suspect_mine_count || 0 }}</span>
          <span v-if="(overview.suspect_mine_count || 0) > 0" class="stat-sub">草林净流向裸建 ≥ 10%</span>
        </div>
      </div>
      <div v-if="hasClassArea" class="class-share">
        <span class="share-title">地类占比（按矢量面积）</span>
        <div class="share-bars">
          <div v-for="row in classShareRows" :key="row.name" class="share-row">
            <span class="share-name">{{ row.name }}</span>
            <div class="share-track">
              <div class="share-fill" :style="{ width: row.percent + '%', background: row.color }"></div>
            </div>
            <span class="share-percent">{{ row.percent.toFixed(1) }}%</span>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import axios from 'axios';
import { computed, onMounted, ref } from 'vue';
import { formatAreaKm2 } from '../utils/areaDisplay.js';

const MINER_API_BASE_URL = import.meta.env.VITE_MINER_API_BASE_URL || '';

const overview = ref(null);
const loading = ref(true);
const error = ref('');

const CLASS_META = [
  { key: 'grassland', name: '草地', color: '#00b894' },
  { key: 'forest', name: '林地', color: '#0a8f5b' },
  { key: 'building', name: '建筑', color: '#ff7675' },
  { key: 'road', name: '道路', color: '#fdcb6e' },
  { key: 'bareground', name: '裸地', color: '#e17055' },
  { key: 'water', name: '水体', color: '#0984e3' },
];

const classShareRows = computed(() => {
  const percent = overview.value?.class_area?.percent || {};
  return CLASS_META.map((meta) => ({ ...meta, percent: Number(percent[meta.key] || 0) }));
});

const hasClassArea = computed(() => (overview.value?.class_area?.total_area_m2 || 0) > 0);

// 同一时刻只发一个概览请求。后端的概览在缓存过期时要重算，重算期间同一 worker 上的其它
// 接口会被拖慢；并发两个请求就是让两个 worker 同时重算（2026-11-24 修复时实测：重算约 9 秒，
// 同一毫秒发出的两个请求各约 8.8 秒，其间其它接口最慢约 670ms，平时约 27ms；随后后端把重算
// 降到约 0.5 秒，见 applications/api/stats.py）。
// 在途期间又被调用（新建/归档后的刷新）不能丢：等在途请求回来后补发一次。
let requestInFlight = null;
let refetchRequested = false;
const fetchOverview = async () => {
  if (requestInFlight) {
    refetchRequested = true;
    return requestInFlight;
  }
  loading.value = true;
  error.value = '';
  requestInFlight = (async () => {
    try {
      const res = await axios.get(`${MINER_API_BASE_URL}/api/stats/overview`);
      overview.value = res.data?.data || {};
    } catch (e) {
      error.value = '统计读取失败，请稍后刷新';
    }
  })();
  try {
    await requestInFlight;
  } finally {
    requestInFlight = null;
    loading.value = false;
  }
  if (refetchRequested) {
    refetchRequested = false;
    await fetchOverview();
  }
};

defineExpose({ fetchOverview });

onMounted(fetchOverview);
</script>

<style scoped>
.stats-panel {
  background: rgba(255, 255, 255, 0.85);
  border: 1px solid rgba(38, 75, 69, 0.15);
  border-radius: 10px;
  padding: 14px 16px;
  margin-bottom: 16px;
}

.stats-loading, .stats-error {
  color: #264b45;
  padding: 12px 0;
  text-align: center;
  font-size: 14px;
}

.stat-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
}

.stat-card {
  background: rgba(38, 75, 69, 0.05);
  border: 1px solid rgba(38, 75, 69, 0.12);
  border-radius: 8px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.stat-card.alert {
  border-color: rgba(225, 112, 85, 0.5);
  background: rgba(225, 112, 85, 0.08);
}

.stat-label { font-size: 12px; color: #5a7d75; }
.stat-value { font-size: 22px; font-weight: 700; color: #264b45; }
.stat-card.alert .stat-value { color: #c0392b; }
.stat-sub { font-size: 11px; color: #7ba39a; }

.class-share { margin-top: 14px; }
.share-title { font-size: 13px; color: #264b45; font-weight: 600; display: block; margin-bottom: 8px; }
.share-bars { display: flex; flex-direction: column; gap: 5px; }
.share-row { display: flex; align-items: center; gap: 8px; }
.share-name { width: 40px; font-size: 12px; color: #5a7d75; }
.share-track { flex: 1; height: 10px; background: rgba(38, 75, 69, 0.08); border-radius: 5px; overflow: hidden; }
.share-fill { height: 100%; border-radius: 5px; }
.share-percent { width: 52px; text-align: right; font-size: 12px; color: #264b45; }
</style>
