<template>
  <section class="parcel-panel">
    <div class="panel-title-row">
      <h2>图斑清单</h2>
      <div class="parcel-tools">
        <input
          v-model="fidFilter"
          type="text"
          placeholder="按矿山 ID 过滤"
          class="fid-input"
          @keyup.enter="loadSummary"
        >
        <button class="secondary-btn" type="button" :disabled="loading" @click="loadSummary">
          {{ loading ? '加载中…' : '刷新' }}
        </button>
      </div>
    </div>
    <p v-if="error" class="error-text">{{ error }}</p>
    <p v-else-if="loading && !items.length" class="empty-block">图斑清单加载中…</p>
    <template v-else>
      <div class="parcel-table">
        <div class="parcel-row head">
          <span>矿山 ID</span><span>名称</span><span>状态</span><span>图斑数</span><span>最新年份</span><span>最近解译</span><span></span>
        </div>
        <div v-for="row in items" :key="row.mine_fid" class="parcel-row">
          <span class="fid">{{ row.mine_fid }}</span>
          <span class="name" :title="row.mine_name || ''">{{ row.mine_name || '—' }}</span>
          <span>{{ row.status || '—' }}</span>
          <span :class="{ zero: !row.feature_count }">{{ row.feature_count }}</span>
          <span>{{ row.latest_year ?? '—' }}</span>
          <span class="time">{{ formatTime(row.latest_analyzed_at) }}</span>
          <button
            class="secondary-btn locate-btn"
            type="button"
            @click="$emit('locate-mine', row.mine_fid)"
          >定位</button>
        </div>
        <p v-if="!items.length" class="empty-block">{{ fidFilter ? '无匹配矿山' : '项目暂无矿山绑定' }}</p>
      </div>
      <div v-if="total > limit" class="pager">
        <button class="secondary-btn" type="button" :disabled="page <= 1 || loading" @click="turn(page - 1)">上一页</button>
        <span class="muted-text">{{ page }} / {{ totalPages }} 页 · 共 {{ total }} 座</span>
        <button class="secondary-btn" type="button" :disabled="page >= totalPages || loading" @click="turn(page + 1)">下一页</button>
      </div>
    </template>
  </section>
</template>

<script setup>
import axios from 'axios';
import { computed, onMounted, ref, watch } from 'vue';

const props = defineProps({
  projectId: { type: Number, required: true },
});
const emit = defineEmits(['locate-mine']);

const MINER_API_BASE_URL = import.meta.env.VITE_MINER_API_BASE_URL || '';

const items = ref([]);
const total = ref(0);
const page = ref(1);
const limit = 20;
const loading = ref(false);
const error = ref('');
const fidFilter = ref('');

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / limit)));

const formatTime = (value) => {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleDateString('zh-CN');
};

const loadSummary = async () => {
  loading.value = true;
  error.value = '';
  try {
    const params = new URLSearchParams({ page: String(page.value), limit: String(limit) });
    if (fidFilter.value.trim()) params.set('fid', fidFilter.value.trim());
    const res = await axios.get(
      `${MINER_API_BASE_URL}/api/projects/${props.projectId}/mines/parcel-summary?${params}`,
    );
    const data = res.data?.data || {};
    items.value = data.items || [];
    total.value = data.count || 0;
    if (page.value > totalPages.value) page.value = totalPages.value;
  } catch (e) {
    error.value = e?.response?.data?.msg || '图斑清单读取失败';
  } finally {
    loading.value = false;
  }
};

const turn = (next) => {
  page.value = next;
  loadSummary();
};

watch(() => props.projectId, () => {
  page.value = 1;
  fidFilter.value = '';
  loadSummary();
});

onMounted(loadSummary);
</script>

<style scoped>
.parcel-panel {
  background: #fff;
  border: 1px solid rgba(38, 75, 69, 0.15);
  border-radius: 10px;
  padding: 16px 18px;
}
.panel-title-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
.panel-title-row h2 { margin: 0; font-size: 17px; color: #264b45; }
.parcel-tools { display: flex; gap: 8px; align-items: center; }
.fid-input {
  padding: 6px 10px; border-radius: 6px; border: 1px solid rgba(38, 75, 69, 0.3);
  width: 140px; font-size: 13px;
}

.parcel-table { border: 1px solid rgba(38, 75, 69, 0.12); border-radius: 8px; overflow: hidden; max-height: 320px; overflow-y: auto; }
.parcel-row {
  display: grid; grid-template-columns: 0.8fr 1.6fr 0.9fr 0.6fr 0.7fr 1fr 0.6fr;
  gap: 8px; padding: 8px 12px; font-size: 13px; align-items: center;
  border-bottom: 1px solid rgba(38, 75, 69, 0.06);
}
.parcel-row.head { background: rgba(38, 75, 69, 0.06); font-weight: 600; position: sticky; top: 0; }
.parcel-row:not(.head):hover { background: rgba(38, 75, 69, 0.04); }
.fid { font-weight: 700; color: #2f6f61; }
.name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.zero { color: #b0b8b5; }
.time { color: #5a7d75; font-size: 12px; }
.locate-btn { padding: 3px 10px; font-size: 12px; }

.pager { display: flex; justify-content: center; align-items: center; gap: 12px; margin-top: 10px; }
.muted-text { color: #7ba39a; font-size: 12px; }

.secondary-btn {
  background: #fff; border: 1px solid #2f6f61; color: #2f6f61;
  border-radius: 6px; padding: 5px 12px; cursor: pointer; font-size: 12px;
}
.secondary-btn:hover { background: rgba(47, 111, 97, 0.08); }
.secondary-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.error-text { color: #c0392b; font-size: 13px; }
.empty-block { color: #7ba39a; font-size: 13px; text-align: center; padding: 16px 0; }
</style>
