<template>
  <transition name="fade">
    <div v-if="visible" class="modal-overlay" @click.self="$emit('close')">
      <div class="modal-content glass-panel">
        <div class="modal-header">
          <h3>新增矿山与解译任务</h3>
          <button class="close-btn" @click="$emit('close')">×</button>
        </div>
        <div class="modal-body">
          <div class="form-group">
            <label>KML 文件路径（服务器路径）</label>
            <input v-model="formData.kmlPath" placeholder="例如: /app/backend/data/new_mine.kml（可选）" class="form-input" />
            <small class="tip">提供 KML 时将自动提取多边形并计算指数；留空则处理默认 KML。</small>
            <div class="upload-row">
              <input ref="kmlFileRef" type="file" accept=".kml" class="file-input" @change="handleKmlFileChange" />
              <button class="btn-upload" type="button" :disabled="uploadingKml" @click="triggerKmlPick">
                {{ uploadingKml ? '上传中...' : '选择并上传 KML' }}
              </button>
              <small v-if="uploadedKmlPath" class="tip">已上传：{{ uploadedKmlPath }}</small>
            </div>
          </div>
          <div class="form-group">
            <label>基准影像路径（Old TIF）</label>
            <input v-model="formData.oldTifPath" placeholder="例如: /app/backend/bianhua_2years/mine_TEST.tif" class="form-input" />
          </div>
          <div class="form-group">
            <label>最新影像路径（New TIF）</label>
            <input v-model="formData.newTifPath" placeholder="默认与基准影像相同" class="form-input" />
          </div>

          <div class="form-row">
            <div class="form-group half">
              <label>单年份设置</label>
              <input v-model="formData.singleYear" placeholder="例如: 2024" class="form-input" />
            </div>
            <div class="form-group half">
              <label>计算设备</label>
              <select v-model="formData.device" class="form-input">
                <option value="auto">自动（优先 NVIDIA GPU）</option>
                <option value="cpu">仅 CPU</option>
              </select>
            </div>
          </div>

          <div class="form-row">
            <div class="form-group half">
              <label>基准年份（Old Year）</label>
              <input v-model="formData.oldYear" placeholder="可选" class="form-input" />
            </div>
            <div class="form-group half">
              <label>最新年份（New Year）</label>
              <input v-model="formData.newYear" placeholder="可选" class="form-input" />
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
            <button class="btn-cancel" @click="$emit('close')" :disabled="running">取消</button>
            <button class="btn-submit" @click="handleSubmit" :disabled="running || !formData.oldTifPath">
              {{ running ? '计算中...' : '开始解译与计算' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </transition>
</template>

<script setup>
import { defineProps, defineEmits, reactive, ref, watch } from 'vue';
import axios from 'axios';

const props = defineProps({
  visible: Boolean,
  running: Boolean,
  error: String,
  result: Object
});

const emit = defineEmits(['close', 'submit']);

const formData = reactive({
  kmlPath: '',
  oldTifPath: '/app/backend/bianhua_2years/mine_TEST.tif',
  newTifPath: '/app/backend/bianhua_2years/mine_TEST.tif',
  singleYear: '',
  oldYear: '',
  newYear: '',
  device: 'auto'
});

const errorMsg = ref('');
const successMsg = ref('');
const uploadingKml = ref(false);
const uploadedKmlPath = ref('');
const kmlFileRef = ref(null);

watch(() => props.visible, (val) => {
  if (val) {
    errorMsg.value = '';
    successMsg.value = '';
  }
});

watch(() => props.error, (val) => {
  if (val) errorMsg.value = val;
});

watch(() => props.result, (val) => {
  if (val && ['succeeded', 'succeeded_with_fallback', 'partial_failed'].includes(val.status)) {
    const summary = val.result || {};
    const count = summary.written_fid_list ? summary.written_fid_list.length : Number(summary.written_fids || 0);
    const kmlUpdated = Number(summary?.kml_update?.updated || 0);
    const kmlInserted = Number(summary?.kml_update?.inserted || 0);
    const changedCount = kmlUpdated + kmlInserted;
    if (changedCount > 0) {
      successMsg.value = `任务完成：KML 已更新矿山信息（更新 ${kmlUpdated}，新增 ${kmlInserted}），并处理 ${count} 个解译结果。`;
    } else {
      successMsg.value = `解译完成：成功处理 ${count} 个矿山多边形并已更新光谱指数。`;
    }
  }
});

const handleSubmit = () => {
  errorMsg.value = '';
  successMsg.value = '';
  const yearFields = [
    ['单年份设置', formData.singleYear],
    ['基准年份', formData.oldYear],
    ['最新年份', formData.newYear],
  ];
  const invalid = yearFields.find(([, value]) => {
    const text = String(value || '').trim();
    return text && !/^\d{4}$/.test(text);
  });
  if (invalid) {
    errorMsg.value = `${invalid[0]}必须为4位年份，例如 2025`;
    return;
  }
  emit('submit', { ...formData });
};

const triggerKmlPick = () => {
  if (kmlFileRef.value) kmlFileRef.value.click();
};

const handleKmlFileChange = async (e) => {
  const file = e?.target?.files?.[0];
  if (!file) return;
  const name = String(file.name || '');
  if (!name.toLowerCase().endsWith('.kml')) {
    errorMsg.value = '只支持 .kml 文件';
    return;
  }
  uploadingKml.value = true;
  errorMsg.value = '';
  uploadedKmlPath.value = '';
  try {
    const rawBase = import.meta.env.VITE_MINER_API_BASE_URL;
    const base = rawBase ? String(rawBase).replace(/\/$/, '') : '';
    const apiUrl = (p) => `${base}${p}`;
    const content = await file.text();
    const res = await axios.post(apiUrl('/api/kml/upload'), { filename: name, content });
    const kmlPath = res?.data?.kml_path;
    if (!kmlPath) throw new Error('KML 上传失败');
    uploadedKmlPath.value = kmlPath;
    formData.kmlPath = kmlPath;
  } catch (err) {
    errorMsg.value = err?.response?.data?.error || err?.message || 'KML 上传失败';
  } finally {
    uploadingKml.value = false;
    if (kmlFileRef.value) kmlFileRef.value.value = '';
  }
};
</script>

<style scoped>
.modal-overlay {
  position: fixed;
  top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0,0,0,0.7);
  z-index: 4000;
  display: flex;
  align-items: center;
  justify-content: center;
}
.modal-content {
  width: 550px;
  background: #0a1929;
  border: 1px solid #4ecdc4;
  box-shadow: 0 0 30px rgba(78, 205, 196, 0.2);
  border-radius: 8px;
}
.modal-header {
  display: flex;
  justify-content: space-between;
  border-bottom: 1px solid rgba(255,255,255,0.1);
  padding: 15px 20px;
}
.modal-header h3 { margin: 0; color: #4ecdc4; font-size: 16px; }
.close-btn { background: none; border: none; color: #fff; font-size: 24px; cursor: pointer; }

.modal-body { padding: 20px; }

.form-group { margin-bottom: 15px; }
.form-row { display: flex; gap: 15px; }
.half { flex: 1; }

.form-group label {
  display: block;
  font-size: 13px;
  color: #8da3b6;
  margin-bottom: 6px;
}
.form-input {
  width: 100%;
  background: rgba(0,0,0,0.3);
  border: 1px solid rgba(255,255,255,0.2);
  color: #fff;
  padding: 8px 10px;
  border-radius: 4px;
  box-sizing: border-box;
}
.form-input:focus {
  border-color: #4ecdc4;
  outline: none;
}
.tip { font-size: 11px; color: #666; margin-top: 4px; display: block; }
.upload-row { margin-top: 10px; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.file-input { display: none; }
.btn-upload {
  background: rgba(78, 205, 196, 0.15);
  border: 1px solid rgba(78, 205, 196, 0.35);
  color: #4ecdc4;
  padding: 6px 10px;
  border-radius: 6px;
  cursor: pointer;
}
.btn-upload:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.error-msg { color: #ff7675; font-size: 13px; margin-bottom: 15px; padding: 10px; background: rgba(255, 118, 117, 0.1); border-radius: 4px; }
.success-msg { color: #00b894; font-size: 13px; margin-bottom: 15px; padding: 10px; background: rgba(0, 184, 148, 0.1); border-radius: 4px; }
.task-status { color: #c8d6e5; font-size: 12px; margin-bottom: 15px; padding: 10px; background: rgba(78, 205, 196, 0.08); border-radius: 4px; line-height: 1.7; }
.fallback-msg { color: #feca57; }

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 20px;
  border-top: 1px solid rgba(255,255,255,0.1);
  padding-top: 15px;
}

.btn-cancel, .btn-submit {
  padding: 8px 16px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 13px;
  border: none;
}
.btn-cancel {
  background: rgba(255,255,255,0.1);
  color: #fff;
}
.btn-submit {
  background: #4ecdc4;
  color: #000;
  font-weight: bold;
}
.btn-submit:disabled {
  background: #2a7a75;
  color: #ccc;
  cursor: not-allowed;
}

.fade-enter-active, .fade-leave-active { transition: opacity 0.2s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
