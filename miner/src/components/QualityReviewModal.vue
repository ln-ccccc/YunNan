<template>
  <transition name="fade">
    <div v-if="visible" class="modal-overlay" @click.self="$emit('close')">
      <div class="modal-content glass-panel">
        <div class="modal-header">
          <h3>智能复核 · 矿山 {{ resultLabel }}</h3>
          <button class="close-btn" type="button" @click="$emit('close')">×</button>
        </div>

        <div class="modal-body">
          <p class="review-note">
            结构性质检线索：自动筛查细碎图斑等疑似误判位置，供人工复核；不构成识别精度判定。
          </p>

          <div class="threshold-row">
            <label for="review-threshold">细碎阈值（m²）</label>
            <input
              id="review-threshold"
              v-model.number="threshold"
              class="form-input threshold-input"
              type="number"
              min="1"
              step="50"
            />
            <button class="btn-refresh" type="button" :disabled="loading" @click="loadReview">重新筛查</button>
          </div>

          <p v-if="loading" class="empty-block">质检分析中…</p>
          <p v-else-if="error" class="error-msg">{{ error }}</p>
          <template v-else-if="report">
            <div class="stat-grid">
              <div class="stat-cell">
                <span class="stat-value">{{ report.feature_count }}</span>
                <span class="stat-label">图斑总数</span>
              </div>
              <div class="stat-cell">
                <span class="stat-value">{{ report.class_kind_count }}</span>
                <span class="stat-label">地类数</span>
              </div>
              <div class="stat-cell">
                <span class="stat-value">{{ formatArea(report.total_area_m2) }}</span>
                <span class="stat-label">合计面积（m²）</span>
              </div>
              <div class="stat-cell" :class="{ warn: report.small_feature_count > 0 }">
                <span class="stat-value">{{ report.small_feature_count }}</span>
                <span class="stat-label">细碎图斑（{{ report.small_feature_ratio_percent }}%）</span>
              </div>
            </div>

            <h4 class="section-title">地类构成</h4>
            <table v-if="report.class_stats.length" class="review-table">
              <thead>
                <tr><th>类别</th><th>图斑数</th><th>面积（m²）</th><th>面积占比</th></tr>
              </thead>
              <tbody>
                <tr v-for="row in report.class_stats" :key="String(row.class_code)">
                  <td>{{ formatClass(row) }}</td>
                  <td>{{ row.count }}</td>
                  <td>{{ formatArea(row.area_m2) }}</td>
                  <td>{{ row.area_percent === null ? '--' : `${row.area_percent}%` }}</td>
                </tr>
              </tbody>
            </table>
            <p v-else class="empty-block">该成果暂无矢量数据（{{ vectorStatusLabel }}）。</p>

            <template v-if="report.class_stats.length">
              <h4 class="section-title">
                疑似细碎图斑
                <span v-if="report.suspects_truncated" class="truncated-hint">（仅显示前 {{ report.suspects.length }} 条）</span>
              </h4>
              <table v-if="report.suspects.length" class="review-table">
                <thead>
                  <tr><th>#</th><th>类别</th><th>面积（m²）</th><th>质心坐标</th></tr>
                </thead>
                <tbody>
                  <tr v-for="row in report.suspects" :key="row.feature_index">
                    <td>{{ row.feature_index }}</td>
                    <td>{{ formatClass(row) }}</td>
                    <td>{{ formatArea(row.area_m2) }}</td>
                    <td class="mono">{{ formatCentroid(row.centroid) }}</td>
                  </tr>
                </tbody>
              </table>
              <p v-else class="empty-block">当前阈值下未发现细碎图斑。</p>

              <p v-if="editable" class="review-note">
                修订请进入 GeoView 编辑器：选中对应图斑修改地类或删除细碎图斑后保存新版本。
              </p>
            </template>
          </template>
        </div>

        <div class="modal-footer">
          <button class="btn-cancel" type="button" @click="$emit('close')">关闭</button>
          <button
            v-if="editable"
            class="btn-submit"
            type="button"
            @click="$emit('open-editor')"
          >去编辑器修订</button>
        </div>
      </div>
    </div>
  </transition>
</template>

<script setup>
import axios from 'axios';
import { ref, watch } from 'vue';

const props = defineProps({
  visible: { type: Boolean, default: false },
  projectId: { type: [Number, String], default: null },
  resultId: { type: [Number, String], default: null },
  resultLabel: { type: String, default: '' },
  vectorStatusLabel: { type: String, default: '' },
  editable: { type: Boolean, default: false },
});

const emit = defineEmits(['close', 'open-editor']);

const MINER_API_BASE_URL = import.meta.env.VITE_MINER_API_BASE_URL || '';

const loading = ref(false);
const error = ref('');
const report = ref(null);
const threshold = ref(100);

const loadReview = async () => {
  if (!props.projectId || !props.resultId) return;
  loading.value = true;
  error.value = '';
  try {
    const res = await axios.get(
      `${MINER_API_BASE_URL}/api/projects/${props.projectId}/classification-results/${props.resultId}/quality-review`,
      { params: { small_area_threshold_m2: threshold.value || undefined } },
    );
    report.value = res.data?.data || null;
  } catch (e) {
    error.value = e?.response?.data?.msg || '质检数据读取失败';
  } finally {
    loading.value = false;
  }
};

watch(
  () => [props.visible, props.resultId],
  ([visible]) => {
    if (visible) {
      report.value = null;
      threshold.value = 100;
      loadReview();
    }
  },
  { immediate: true },
);

const formatArea = (value) => {
  if (value === null || value === undefined) return '--';
  return Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
};

const formatClass = (row) => {
  const name = row.class_name || '';
  return row.class_code === null || row.class_code === undefined
    ? (name || '未知类别')
    : (name ? `${row.class_code} · ${name}` : `类别 ${row.class_code}`);
};

const formatCentroid = (centroid) => {
  if (!Array.isArray(centroid) || centroid.length < 2) return '--';
  return `${centroid[0]}, ${centroid[1]}`;
};
</script>

<style scoped>
.modal-overlay {
  position: fixed; inset: 0; background: rgba(15, 35, 31, 0.55);
  display: flex; align-items: center; justify-content: center; z-index: 60;
}
.modal-content {
  width: min(720px, 92vw); max-height: 84vh; display: flex; flex-direction: column;
  background: #f4f8f6; border-radius: 12px; border: 1px solid rgba(38, 75, 69, 0.2);
}
.modal-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 18px; border-bottom: 1px solid rgba(38, 75, 69, 0.12);
}
.modal-header h3 { margin: 0; font-size: 16px; color: #264b45; }
.close-btn { background: none; border: none; font-size: 20px; cursor: pointer; color: #5a7d75; }
.modal-body { padding: 14px 18px; overflow-y: auto; }
.modal-footer {
  display: flex; justify-content: flex-end; gap: 10px;
  padding: 12px 18px; border-top: 1px solid rgba(38, 75, 69, 0.12);
}

.review-note { margin: 0 0 10px; font-size: 12px; color: #5a7d75; }
.threshold-row { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
.threshold-row label { font-size: 13px; color: #264b45; }
.threshold-input { width: 110px; padding: 6px 8px; border: 1px solid rgba(38, 75, 69, 0.3); border-radius: 6px; }
.btn-refresh {
  background: #fff; border: 1px solid rgba(38, 75, 69, 0.35); color: #2f6f61;
  border-radius: 6px; padding: 6px 14px; cursor: pointer; font-size: 12px;
}
.btn-refresh:disabled { opacity: 0.55; cursor: not-allowed; }

.stat-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 14px; }
.stat-cell {
  background: #fff; border: 1px solid rgba(38, 75, 69, 0.12); border-radius: 8px;
  padding: 10px; display: flex; flex-direction: column; gap: 4px; text-align: center;
}
.stat-cell.warn { border-color: rgba(180, 60, 47, 0.45); }
.stat-value { font-size: 18px; font-weight: 600; color: #264b45; }
.stat-cell.warn .stat-value { color: #b43c2f; }
.stat-label { font-size: 11px; color: #5a7d75; }

.section-title { margin: 12px 0 8px; font-size: 13px; color: #264b45; }
.truncated-hint { font-weight: 400; font-size: 11px; color: #5a7d75; }
.review-table { width: 100%; border-collapse: collapse; font-size: 12px; background: #fff; }
.review-table th, .review-table td {
  border: 1px solid rgba(38, 75, 69, 0.12); padding: 6px 8px; text-align: left; color: #264b45;
}
.review-table th { background: rgba(47, 111, 97, 0.08); }
.mono { font-family: monospace; }

.empty-block { margin: 14px 0; text-align: center; color: #5a7d75; font-size: 13px; }
.error-msg { margin: 10px 0; color: #b43c2f; font-size: 13px; }
.btn-cancel {
  background: #fff; border: 1px solid rgba(38, 75, 69, 0.35); color: #264b45;
  border-radius: 6px; padding: 8px 18px; cursor: pointer; font-size: 13px;
}
.btn-submit {
  background: #2f6f61; color: #fff; border: none; border-radius: 6px;
  padding: 8px 18px; cursor: pointer; font-size: 13px;
}
</style>
