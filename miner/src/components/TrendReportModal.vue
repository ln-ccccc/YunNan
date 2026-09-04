<template>
  <transition name="fade">
    <div v-if="visible" class="modal-overlay" @click.self="$emit('close')">
      <div class="modal-content glass-panel">
        <div class="modal-header">
          <h3>全矿山变化趋势统计</h3>
          <button class="close-btn" @click="$emit('close')">×</button>
        </div>

        <div class="toolbar">
          <select v-model="selectedClassName" class="select">
            <option v-for="opt in classOptions" :key="opt.key" :value="opt.key">
              {{ opt.label }}
            </option>
          </select>
          <select v-model="selectedDirection" class="select">
            <option value="upward">上升</option>
            <option value="downward">下降</option>
            <option value="stable">稳定</option>
            <option value="all">全部</option>
          </select>
          <button class="btn" :disabled="loading" @click="emitRefresh">
            {{ loading ? '加载中...' : '刷新统计' }}
          </button>
          <button class="btn primary" :disabled="loading || !report" @click="emitExport">
            导出 CSV
          </button>
        </div>

        <div v-if="error" class="error-msg">{{ error }}</div>

        <div v-if="report" class="summary-grid">
          <div class="summary-item">
            <div class="label">矿山总数</div>
            <div class="value">{{ report.mine_total || 0 }}</div>
          </div>
          <div class="summary-item">
            <div class="label">可统计矿山数</div>
            <div class="value">{{ report.coverage?.matrix_ready_count || 0 }}</div>
          </div>
          <div class="summary-item">
            <div class="label">矩阵缺失数</div>
            <div class="value">{{ report.coverage?.matrix_missing_count || 0 }}</div>
          </div>
          <div class="summary-item">
            <div class="label">{{ currentClassLabel }}上升数</div>
            <div class="value">{{ report.class_trends?.selected_class?.upward_count || 0 }}</div>
          </div>
          <div class="summary-item compact">
            <div class="label">{{ currentClassLabel }}下降数</div>
            <div class="value">{{ report.class_trends?.selected_class?.downward_count || 0 }}</div>
          </div>
          <div class="summary-item compact">
            <div class="label">{{ currentClassLabel }}稳定数</div>
            <div class="value">{{ report.class_trends?.selected_class?.stable_count || 0 }}</div>
          </div>
        </div>

        <div class="table-title">{{ tableTitle }}</div>
        <div class="table-wrap">
          <table class="data-table">
            <thead>
              <tr>
                <th>FID</th>
                <th>矿山名称</th>
                <th>起始年份</th>
                <th>结束年份</th>
                <th>起始占比(%)</th>
                <th>结束占比(%)</th>
                <th>变化量(%)</th>
                <th>矿山面积(km²)</th>
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
                <td>{{ formatAreaKm2(deriveMineAreaKm2(row)) }}</td>
                <td>{{ formatAreaKm2(getStartAreaKm2(row)) }}</td>
                <td>{{ formatAreaKm2(getEndAreaKm2(row)) }}</td>
                <td :class="deltaClass(getDeltaAreaKm2(row))">{{ formatAreaKm2(getDeltaAreaKm2(row)) }}</td>
              </tr>
              <tr v-if="!selectedRows.length">
                <td colspan="11" class="empty">{{ emptyText }}</td>
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
    { key: 'grassland', label: '草地' },
    { key: 'forest', label: '林地' },
    { key: 'building', label: '建筑' },
    { key: 'road', label: '道路' },
    { key: 'bareground', label: '裸土' },
    { key: 'water', label: '水体' }
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
  if (selectedDirection.value === 'upward') return '上升';
  if (selectedDirection.value === 'downward') return '下降';
  if (selectedDirection.value === 'stable') return '稳定';
  return '全部';
});

const tableTitle = computed(() => `${currentClassLabel.value}占比${directionLabel.value} FID 明细`);

const emptyText = computed(() => {
  if (!props.report) return '暂无统计结果';
  const ready = Number(props.report?.coverage?.matrix_ready_count || 0);
  if (ready === 0) return '暂无可统计的分类比例数据';
  return '暂无符合当前筛选条件的记录';
});

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
