<template>
  <transition name="fade">
    <div v-if="visible" class="modal-overlay" @click.self="$emit('close')">
      <div class="modal-content glass-panel">
        <div class="modal-header">
          <h3>项目地物分类</h3>
          <button class="close-btn" type="button" aria-label="关闭推理窗口" @click="$emit('close')">×</button>
        </div>

        <div class="modal-body">
          <div class="form-group">
            <label for="inference-imagery">推理影像</label>
            <select id="inference-imagery" v-model="formData.inputAssetId" class="form-input" :disabled="assetsLoading">
              <option value="">{{ assetsLoading ? '正在加载可用影像…' : '请选择已就绪的影像或当前底图原影像' }}</option>
              <option v-for="asset in imageryAssets" :key="asset.id" :value="asset.id">
                {{ formatAssetLabel(asset) }}
              </option>
            </select>
            <small v-if="!assetsLoading && !imageryAssets.length" class="tip">请先登记影像，或激活保留原影像的项目底图。</small>
          </div>

          <div class="form-row">
            <div class="form-group half">
              <label for="inference-year">年份（底图原影像必填，登记影像可留空）</label>
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
            <button class="btn-submit" type="button" data-testid="inference-submit" :disabled="running || assetsLoading || !formData.inputAssetId" @click="handleSubmit">
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
  inputAssetId: '',
  year: '',
  device: 'auto',
});
const errorMsg = ref('');
const successMsg = ref('');

watch(() => props.visible, (visible) => {
  if (!visible) return;
  errorMsg.value = '';
  successMsg.value = '';
  formData.inputAssetId = '';
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
  if (!formData.inputAssetId) {
    errorMsg.value = '请选择已就绪的推理影像。';
    return;
  }
  emit('submit', {
    inputAssetId: formData.inputAssetId,
    year,
    device: formData.device || 'auto',
  });
}
</script>

<style scoped>
.modal-overlay { position: fixed; inset: 0; z-index: 4000; display: flex; align-items: center; justify-content: center; background: rgba(22, 56, 60, 0.26); }
.modal-content { width: min(550px, calc(100vw - 32px)); max-height: calc(100dvh - 32px); overflow-y: auto; border: 1px solid var(--glass-border); border-radius: 24px; background: var(--glass-surface); color: var(--glass-ink); box-shadow: var(--glass-shadow); backdrop-filter: blur(20px) saturate(125%); -webkit-backdrop-filter: blur(20px) saturate(125%); }
.modal-header { display: flex; align-items: center; justify-content: space-between; padding: 18px 22px; border-bottom: 1px solid rgba(35, 70, 73, 0.12); }
.modal-header h3 { margin: 0; color: var(--glass-ink); font-size: 18px; font-weight: 600; }
.close-btn { width: 36px; height: 36px; border: 0; border-radius: 10px; background: rgba(255, 255, 255, 0.64); color: var(--glass-muted); font-size: 24px; cursor: pointer; }
.modal-body { padding: 20px; }
.form-group { margin-bottom: 15px; }
.form-row { display: flex; gap: 15px; }
.half { flex: 1; }
.form-group label { display: block; margin-bottom: 6px; color: var(--glass-muted); font-size: 13px; }
.form-input { box-sizing: border-box; width: 100%; padding: 10px 12px; border: 1px solid #cadbd5; border-radius: 10px; outline: none; background: rgba(255, 255, 255, 0.9); color: var(--glass-ink); transition: border-color var(--motion-control) ease, box-shadow var(--motion-control) ease; }
.form-input:focus { border-color: var(--glass-accent); box-shadow: 0 0 0 3px rgba(36, 108, 89, 0.16); }
.tip { display: block; margin-top: 6px; color: var(--glass-muted); font-size: 12px; }
.error-msg, .success-msg, .task-status { margin-bottom: 15px; padding: 12px; border-radius: 10px; font-size: 13px; animation: inference-status var(--motion-control) ease; }
.error-msg { background: #fff0ed; color: #a43f32; }
.success-msg { background: #e0ede6; color: var(--glass-accent); }
.task-status { background: #edf5f1; color: var(--glass-ink); font-size: 12px; line-height: 1.7; }
.fallback-msg { color: #886017; }
.modal-footer { display: flex; justify-content: flex-end; gap: 10px; margin-top: 20px; padding-top: 15px; border-top: 1px solid rgba(35, 70, 73, 0.12); }
.btn-cancel, .btn-submit { border: 1px solid transparent; border-radius: 12px; padding: 10px 16px; font-size: 13px; cursor: pointer; transition: background-color var(--motion-control) ease, box-shadow var(--motion-control) ease, transform var(--motion-fast) var(--motion-ease); }
.btn-cancel { background: rgba(255, 255, 255, 0.85); border-color: #cadbd5; color: var(--glass-accent); }
.btn-submit { background: var(--glass-accent); color: #fff; font-weight: 600; box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.18), 0 3px 8px rgba(36, 108, 89, 0.12); }
.close-btn:hover, .btn-cancel:hover:not(:disabled) { background: #e0ede6; }
.btn-submit:hover:not(:disabled) { box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.18), 0 5px 14px rgba(36, 108, 89, 0.2); }
.close-btn:active, .btn-cancel:active:not(:disabled), .btn-submit:active:not(:disabled) { transform: scale(0.97); }
.close-btn:focus-visible, .btn-cancel:focus-visible, .btn-submit:focus-visible { outline: 3px solid rgba(36, 108, 89, 0.32); outline-offset: 3px; }
.btn-submit:disabled, .btn-cancel:disabled { cursor: not-allowed; opacity: 0.55; }
.fade-enter-active, .fade-leave-active { transition: opacity var(--motion-panel) ease; }
.fade-enter-active .modal-content, .fade-leave-active .modal-content { transition: transform var(--motion-panel) var(--motion-ease); }
.fade-enter-from, .fade-leave-to { opacity: 0; }
.fade-enter-from .modal-content, .fade-leave-to .modal-content { transform: translateY(8px) scale(0.98); }
@keyframes inference-status { from { opacity: 0.35; } to { opacity: 1; } }
@supports not ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))) { .modal-content { background: #f8fbfa; } }
@media (prefers-reduced-motion: reduce) { .modal-overlay, .modal-content, .form-input, .close-btn, .btn-cancel, .btn-submit, .error-msg, .success-msg, .task-status { transition: none !important; animation: none !important; transform: none !important; } }
@media (max-width: 560px) { .form-row { flex-direction: column; gap: 0; } }
</style>
