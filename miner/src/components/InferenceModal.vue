<template>
  <transition name="fade">
    <div v-if="visible" class="modal-overlay" @click.self="$emit('close')">
      <div class="modal-content glass-panel">
        <div class="modal-header">
          <h3>项目地物分类</h3>
          <button class="close-btn" type="button" @click="$emit('close')">×</button>
        </div>

        <div class="modal-body">
          <div class="form-group">
            <label for="inference-imagery">推理影像</label>
            <select id="inference-imagery" v-model="formData.datasetId" class="form-input" :disabled="assetsLoading">
              <option value="">{{ assetsLoading ? '正在加载已登记影像…' : '请选择已登记且已就绪的影像' }}</option>
              <option v-for="asset in imageryAssets" :key="asset.id" :value="String(asset.source_id)">
                {{ formatAssetLabel(asset) }}
              </option>
            </select>
            <small v-if="!assetsLoading && !imageryAssets.length" class="tip">请先通过项目工作台登记并准备影像。</small>
          </div>

          <div class="form-row">
            <div class="form-group half">
              <label for="inference-year">年份（不填则用影像登记年份）</label>
              <input id="inference-year" v-model="formData.year" placeholder="例如：2024" class="form-input" inputmode="numeric" />
            </div>
            <div class="form-group half">
              <label for="inference-device">计算设备</label>
              <select id="inference-device" v-model="formData.device" class="form-input">
                <option value="auto">自动（优先 NVIDIA GPU）</option>
                <option value="cpu">仅 CPU</option>
              </select>
            </div>
          </div>

          <div v-if="result" class="task-status">
            <div>任务状态：{{ result.status || '--' }}</div>
            <div v-if="result.progress">进度：{{ result.progress.current || 0 }} / {{ result.progress.total || 0 }}</div>
            <div v-if="result.effective_device">实际设备：{{ result.effective_device }}</div>
            <div v-if="result.fallback_reason" class="fallback-msg">降级原因：{{ result.fallback_reason }}</div>
          </div>
          <div v-if="errorMsg" class="error-msg">{{ errorMsg }}</div>
          <div v-if="successMsg" class="success-msg">{{ successMsg }}</div>

          <div class="modal-footer">
            <button class="btn-cancel" type="button" :disabled="running" @click="$emit('close')">取消</button>
            <button class="btn-submit" type="button" data-testid="inference-submit" :disabled="running || assetsLoading || !formData.datasetId" @click="handleSubmit">
              {{ running ? '正在计算…' : '确认开始推理' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </transition>
</template>

<script setup>
import { defineEmits, defineProps, reactive, ref, watch } from 'vue';

const props = defineProps({
  visible: Boolean,
  running: Boolean,
  error: String,
  result: Object,
  imageryAssets: {
    type: Array,
    default: () => [],
  },
  assetsLoading: Boolean,
});

const emit = defineEmits(['close', 'load-imagery', 'submit']);

const formData = reactive({
  datasetId: '',
  year: '',
  device: 'auto',
});
const errorMsg = ref('');
const successMsg = ref('');

watch(() => props.visible, (visible) => {
  if (!visible) return;
  errorMsg.value = '';
  successMsg.value = '';
  formData.datasetId = '';
  emit('load-imagery');
});

watch(() => props.error, (value) => {
  if (value) errorMsg.value = value;
});

watch(() => props.result, (value) => {
  if (!value || !['succeeded', 'succeeded_with_fallback', 'partial_failed'].includes(value.status)) return;
  const summary = value.result || {};
  const count = Array.isArray(summary.written_fid_list)
    ? summary.written_fid_list.length
    : Number(summary.written_fids || 0);
  successMsg.value = `任务完成：已处理 ${count} 个矿山。`;
});

function formatAssetLabel(asset) {
  const temporal = asset?.temporal || {};
  const dates = [temporal.start, temporal.end].filter(Boolean);
  const timeLabel = dates.length ? ` · ${dates.join(' - ')}` : '';
  return `${asset?.name || '未命名影像'}${timeLabel} · ${asset?.format || '格式未知'}`;
}

function handleSubmit() {
  errorMsg.value = '';
  successMsg.value = '';
  const year = String(formData.year || '').trim();
  if (year && !/^\d{4}$/.test(year)) {
    errorMsg.value = '年份必须为 4 位数字，例如 2024。';
    return;
  }
  if (!formData.datasetId) {
    errorMsg.value = '请选择已登记且已就绪的影像。';
    return;
  }
  emit('submit', {
    datasetId: Number(formData.datasetId),
    year,
    device: formData.device || 'auto',
  });
}
</script>

<style scoped>
.modal-overlay { position: fixed; inset: 0; z-index: 4000; display: flex; align-items: center; justify-content: center; background: rgba(0, 0, 0, 0.7); }
.modal-content { width: min(550px, calc(100vw - 32px)); border: 1px solid #4ecdc4; border-radius: 8px; background: #0a1929; box-shadow: 0 0 30px rgba(78, 205, 196, 0.2); }
.modal-header { display: flex; justify-content: space-between; padding: 15px 20px; border-bottom: 1px solid rgba(255, 255, 255, 0.1); }
.modal-header h3 { margin: 0; color: #4ecdc4; font-size: 16px; }
.close-btn { border: 0; background: none; color: #fff; font-size: 24px; cursor: pointer; }
.modal-body { padding: 20px; }
.form-group { margin-bottom: 15px; }
.form-row { display: flex; gap: 15px; }
.half { flex: 1; }
.form-group label { display: block; margin-bottom: 6px; color: #8da3b6; font-size: 13px; }
.form-input { box-sizing: border-box; width: 100%; padding: 8px 10px; border: 1px solid rgba(255, 255, 255, 0.2); border-radius: 4px; outline: none; background: rgba(0, 0, 0, 0.3); color: #fff; }
.form-input:focus { border-color: #4ecdc4; }
.tip { display: block; margin-top: 4px; color: #8da3b6; font-size: 11px; }
.error-msg, .success-msg, .task-status { margin-bottom: 15px; padding: 10px; border-radius: 4px; font-size: 13px; }
.error-msg { background: rgba(255, 118, 117, 0.1); color: #ff7675; }
.success-msg { background: rgba(0, 184, 148, 0.1); color: #00b894; }
.task-status { background: rgba(78, 205, 196, 0.08); color: #c8d6e5; font-size: 12px; line-height: 1.7; }
.fallback-msg { color: #feca57; }
.modal-footer { display: flex; justify-content: flex-end; gap: 10px; margin-top: 20px; padding-top: 15px; border-top: 1px solid rgba(255, 255, 255, 0.1); }
.btn-cancel, .btn-submit { border: 0; border-radius: 4px; padding: 8px 16px; font-size: 13px; cursor: pointer; }
.btn-cancel { background: rgba(255, 255, 255, 0.1); color: #fff; }
.btn-submit { background: #4ecdc4; color: #000; font-weight: bold; }
.btn-submit:disabled, .btn-cancel:disabled { cursor: not-allowed; opacity: 0.55; }
.fade-enter-active, .fade-leave-active { transition: opacity 0.2s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
@media (max-width: 560px) { .form-row { flex-direction: column; gap: 0; } }
</style>
