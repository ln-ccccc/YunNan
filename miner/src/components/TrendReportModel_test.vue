<template>
  <transition name="fade">
    <div v-if="visible" class="modal-overlay" @click.self="$emit('close')">
      <div class="modal-content glass-panel">
        <div class="modal-header">
          <h3>鍏ㄧ熆灞卞彉鍖栬秼鍔跨粺璁?/h3>
          <button class="close-btn" @click="$emit('close')">x</button>
        </div>

        <div class="toolbar">
          <select v-model="selectedClassName" class="select">
            <option v-for="opt in classOptions" :key="opt.key" :value="opt.key">
              {{ opt.label }}
            </option>
          </select>
          <select v-model="selectedDirection" class="select">
            <option value="upward">涓婂崌</option>
            <option value="downward">涓嬮檷</option>
            <option value="stable">绋冲畾</option>
            <option value="all">鍏ㄩ儴</option>
          </select>
          <button class="btn" :disabled="loading" @click="emitRefresh">
            {{ loading ? '鍔犺浇涓?..' : '鍒锋柊缁熻' }}
          </button>
          <button class="btn primary" :disabled="loading || !report" @click="emitExport">
            瀵煎嚭 Excel
          </button>
        </div>

        <div v-if="error" class="error-msg">{{ error }}</div>

        <div v-if="report" class="summary-grid">
          <div class="summary-item">
            <div class="label">鐭垮北鎬绘暟</div>
            <div class="value">{{ report.mine_total || 0 }}</div>
          </div>
          <div class="summary-item">
            <div class="label">鐭╅樀鍙粺璁℃暟</div>
            <div class="value">{{ report.coverage?.matrix_ready_count || 0 }}</div>
          </div>
          <div class="summary-item">
            <div class="label">鐭╅樀缂哄け鏁?/div>
            <div class="value">{{ report.coverage?.matrix_missing_count || 0 }}</div>
          </div>
          <div class="summary-item">
            <div class="label">{{ currentClassLabel }}涓婂崌鏁?/div>
            <div class="value">{{ report.class_trends?.selected_class?.upward_count || 0 }}</div>
          </div>
          <div class="summary-item compact">
            <div class="label">{{ currentClassLabel }}涓嬮檷鏁?/div>
            <div class="value">{{ report.class_trends?.selected_class?.downward_count || 0 }}</div>
          </div>
          <div class="summary-item compact">
            <div class="label">{{ currentClassLabel }}绋冲畾鏁?/div>
            <div class="value">{{ report.class_trends?.selected_class?.stable_count || 0 }}</div>
          </div>
        </div>

        <div class="table-title">{{ tableTitle }}</div>
        <div class="table-wrap">
          <table class="data-table">
            <thead>
              <tr>
                <th>FID</th>
                <th>鐭垮北鍚嶇О</th>
                <th>璧峰骞翠唤</th>
                <th>缁撴潫骞翠唤</th>
                <th>璧峰鍗犳瘮(%)</th>
                <th>缁撴潫鍗犳瘮(%)</th>
                <th>鍙樺寲閲?%)</th>
                <th>起始面积(km²)</th>
                <th>结束面积(km²)</th>
                <th>变化面积(km²)</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in selectedRows" :key="row.fid">
                <td>{{ row.fid }}</td>
                <td>{{ row.mine_name || '-' }}</td>
                <td>{{ row.start_year }}</td>
                <td>{{ row.end_year }}</td>
                <td>{{ formatNum(row.start_percent, 4) }}</td>
                <td>{{ formatNum(row.end_percent, 4) }}</td>
                <td :class="deltaClass(row.delta_percent)">{{ formatNum(row.delta_percent, 4) }}</td>
                <td>{{ formatAreaKm2(getStartAreaKm2(row)) }}</td>
                <td>{{ formatAreaKm2(getEndAreaKm2(row)) }}</td>
                <td :class="deltaClass(getDeltaAreaKm2(row))">{{ formatAreaKm2(getDeltaAreaKm2(row)) }}</td>
              </tr>
              <tr v-if="!selectedRows.length">
                <td colspan="10" class="empty">鏆傛棤绗﹀悎鏉′欢鐨勮褰?/td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </transition>
</template>

<script setup>
import { computed, ref, watch } from 'vue';

const props = defineProps({
  visible: Boolean,
  loading: Boolean,
  error: String,
  report: Object
});

const emit = defineEmits(['close', 'refresh', 'export']);

const selectedClassName = ref('bareground');
const selectedDirection = ref('downward');

const classOptions = computed(() => {
  const fromApi = props.report?.available_classes;
  if (Array.isArray(fromApi) && fromApi.length) return fromApi;
  return [
    { key: 'grassland', label: '鑽夊湴' },
    { key: 'forest', label: '鏋楀湴' },
    { key: 'building', label: '寤虹瓚' },
    { key: 'road', label: '閬撹矾' },
    { key: 'bareground', label: '瑁稿湡' },
    { key: 'water', label: '姘翠綋' }
  ];
});

watch(
  () => props.report?.filters,
  (filters) => {
    if (!filters) return;
    if (filters.class_name) selectedClassName.value = filters.class_name;
    if (filters.direction) selectedDirection.value = filters.direction;
  },
  { deep: true, immediate: true }
);

const currentClassLabel = computed(() => {
  const key = selectedClassName.value;
  const hit = classOptions.value.find(i => i.key === key);
  return hit?.label || key;
});

const selectedRows = computed(() => props.report?.tables?.selected_class_rows || []);

const directionLabel = computed(() => {
  if (selectedDirection.value === 'upward') return '涓婂崌';
  if (selectedDirection.value === 'downward') return '涓嬮檷';
  if (selectedDirection.value === 'stable') return '绋冲畾';
  return '鍏ㄩ儴';
});

const tableTitle = computed(() => `${currentClassLabel.value}鍗犳瘮${directionLabel.value} FID 鏄庣粏`);

const emitRefresh = () => {
  emit('refresh', {
    class_name: selectedClassName.value,
    direction: selectedDirection.value
  });
};

const emitExport = () => {
  emit('export', {
    class_name: selectedClassName.value,
    direction: selectedDirection.value
  });
};

const formatNum = (v, digits = 2) => {
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(digits) : '-';
};

const deltaClass = (v) => {
  const n = Number(v);
  if (!Number.isFinite(n)) return '';
  return n > 0 ? 'upward' : (n < 0 ? 'downward' : '');
};

const toFinite = (v) => {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

const deriveMineAreaKm2 = (row) => {
  const directKm2 = toFinite(row?.mine_area_km2);
  if (directKm2 !== null) return directKm2;
  const m2 = toFinite(row?.mine_area_m2 ?? row?.mine_area);
  if (m2 !== null) return m2 / 1e6;
  return null;
};

const percentToAreaKm2 = (percent, mineAreaKm2) => {
  const p = toFinite(percent);
  if (p === null || mineAreaKm2 === null) return null;
  return (p / 100) * mineAreaKm2;
};

const getStartAreaKm2 = (row) => {
  const direct = toFinite(row?.start_area_km2);
  if (direct !== null) return direct;
  return percentToAreaKm2(row?.start_percent, deriveMineAreaKm2(row));
};

const getEndAreaKm2 = (row) => {
  const direct = toFinite(row?.end_area_km2);
  if (direct !== null) return direct;
  return percentToAreaKm2(row?.end_percent, deriveMineAreaKm2(row));
};

const getDeltaAreaKm2 = (row) => {
  const direct = toFinite(row?.delta_area_km2);
  if (direct !== null) return direct;
  const s = getStartAreaKm2(row);
  const e = getEndAreaKm2(row);
  if (s === null || e === null) return null;
  return e - s;
};

const formatAreaKm2 = (v) => {
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(4) : '无数据';
};
</script>

<style scoped>
.modal-overlay {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  left: 0;
  background: rgba(0, 0, 0, 0.6);
  z-index: 3200;
  display: flex;
  align-items: center;
  justify-content: center;
}

.modal-content {
  width: min(1000px, 92vw);
  max-height: 86vh;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  background: #0a1929;
  border: 1px solid #4ecdc4;
}

.glass-panel {
  background: rgba(255, 255, 255, 0.03);
  border-radius: 8px;
  padding: 12px;
}

.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}

.close-btn {
  background: none;
  border: none;
  color: #fff;
  font-size: 22px;
  cursor: pointer;
}

.toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 10px;
  flex-wrap: wrap;
}

.btn {
  border: 1px solid rgba(255, 255, 255, 0.2);
  background: rgba(255, 255, 255, 0.06);
  color: #fff;
  border-radius: 6px;
  padding: 6px 12px;
  cursor: pointer;
}

.btn.primary {
  background: rgba(78, 205, 196, 0.2);
  border-color: rgba(78, 205, 196, 0.5);
  color: #4ecdc4;
}

.select {
  border: 1px solid rgba(255, 255, 255, 0.2);
  background: rgba(255, 255, 255, 0.06);
  color: #fff;
  border-radius: 6px;
  padding: 6px 10px;
}

.select option {
  color: #fff;
  background: #0a1929;
}

.btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(120px, 1fr));
  gap: 8px;
  margin-bottom: 12px;
}

.summary-item {
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 6px;
  padding: 8px;
  background: rgba(0, 0, 0, 0.2);
}

.summary-item.compact .value {
  font-size: 16px;
}

.label {
  color: #8da3b6;
  font-size: 12px;
}

.value {
  margin-top: 4px;
  font-size: 18px;
  color: #4ecdc4;
  font-weight: 600;
}

.table-title {
  margin-bottom: 8px;
  font-size: 14px;
  color: #fff;
}

.table-wrap {
  overflow: auto;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 6px;
}

.data-table {
  width: 100%;
  border-collapse: collapse;
  min-width: 1120px;
}

.data-table th,
.data-table td {
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  padding: 8px;
  text-align: left;
  font-size: 12px;
}

.data-table th {
  background: rgba(255, 255, 255, 0.04);
  color: #8da3b6;
}

.downward {
  color: #ff7675;
  font-weight: 600;
}

.upward {
  color: #00b894;
  font-weight: 600;
}

.empty {
  text-align: center;
  color: #8da3b6;
}

.error-msg {
  color: #ff7675;
  margin-bottom: 8px;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
