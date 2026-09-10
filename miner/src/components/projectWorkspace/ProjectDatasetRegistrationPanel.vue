<template>
  <section class="dataset-panel">
    <div class="panel-title-row">
      <div>
        <h2>登记推理影像</h2>
        <p class="muted-text">请先将 TIF/TIFF 影像放入离线导入目录，再填写相对 storage_key。</p>
      </div>
    </div>

    <form class="dataset-form" @submit.prevent="submit">
      <label>
        显示名称
        <input v-model.trim="form.display_name" required placeholder="例如：2024 年春季影像" />
      </label>
      <label>
        相对 storage_key
        <input v-model.trim="form.storage_key" required placeholder="incoming/2024-spring.tif" />
      </label>
      <label>
        源格式
        <input v-model.trim="form.source_format" placeholder="tif" />
      </label>
      <label>
        关联矿山
        <select v-model="form.mine_fid">
          <option value="">关联全部矿山／不指定</option>
          <option v-for="mine in mineOptions" :key="mine.fid" :value="String(mine.fid)">
            {{ mine.name || `矿山 ${mine.fid}` }}
          </option>
        </select>
      </label>
      <label>
        开始年份
        <input v-model.number="form.year_start" type="number" min="1900" max="9999" placeholder="开始年份" />
      </label>
      <label>
        结束年份
        <input v-model.number="form.year_end" type="number" min="1900" max="9999" placeholder="结束年份" />
      </label>
      <label class="full-width">
        切片参数（可选 JSON）
        <textarea v-model.trim="form.slice_config_text" placeholder='例如：{"slice_size":1024,"padding":64}'></textarea>
      </label>
      <div class="form-actions full-width">
        <button class="primary-btn" type="submit" :disabled="busy || !projectId">
          {{ busy ? '登记中…' : '登记影像' }}
        </button>
      </div>
    </form>

    <p v-if="localError || error" class="error-text">{{ localError || error }}</p>
  </section>
</template>

<script setup>
import { reactive, ref, watch } from 'vue';

import { isSafeIncomingTiffStorageKey } from '../../projectWorkspace/projectWorkspaceHelpers.js';

const props = defineProps({
  projectId: {
    type: [Number, String],
    default: null,
  },
  mineOptions: {
    type: Array,
    default: () => [],
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

const emit = defineEmits(['register-dataset']);
const form = reactive(createEmptyForm());
const localError = ref('');

watch(
  () => props.projectId,
  () => {
    Object.assign(form, createEmptyForm());
    localError.value = '';
  },
);

function createEmptyForm() {
  return {
    display_name: '',
    storage_key: '',
    source_format: 'tif',
    mine_fid: '',
    year_start: '',
    year_end: '',
    slice_config_text: '',
  };
}

function submit() {
  localError.value = '';
  const storageKey = normalizeStorageKey(form.storage_key);
  if (!isSafeIncomingTiffStorageKey(storageKey)) {
    localError.value = '请输入 incoming/ 目录下的受支持栅格影像键（tif/tiff/img/jp2）。';
    return;
  }

  const sliceConfig = parseSliceConfig(form.slice_config_text);
  if (sliceConfig === null) return;

  emit('register-dataset', {
    display_name: String(form.display_name || '').trim(),
    dataset_kind: 'imagery',
    storage_key: storageKey,
    source_format: String(form.source_format || '').trim() || null,
    mine_fid: normalizeInteger(form.mine_fid),
    year_start: normalizeInteger(form.year_start),
    year_end: normalizeInteger(form.year_end),
    slice_config_json: sliceConfig,
  });
}

function normalizeStorageKey(value) {
  return String(value || '').trim().replaceAll('\\', '/');
}

function parseSliceConfig(value) {
  if (!value) return {};
  try {
    const parsed = JSON.parse(value);
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
      throw new Error('not an object');
    }
    return parsed;
  } catch (_) {
    localError.value = '切片参数必须是 JSON 对象。';
    return null;
  }
}

function normalizeInteger(value) {
  if (value === '' || value === null || value === undefined) return null;
  const parsed = Number(value);
  return Number.isInteger(parsed) ? parsed : null;
}
</script>

<style scoped>
.dataset-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.panel-title-row h2,
.panel-title-row p,
.error-text {
  margin: 0;
}

.dataset-form {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

label {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.full-width {
  grid-column: 1 / -1;
}

input,
select,
textarea,
button {
  font: inherit;
}

input,
select,
textarea {
  box-sizing: border-box;
  width: 100%;
  border: 1px solid rgba(35, 86, 78, 0.18);
  border-radius: 10px;
  padding: 9px 11px;
}

textarea {
  min-height: 90px;
  resize: vertical;
}

.form-actions {
  display: flex;
}

.primary-btn {
  border: none;
  border-radius: 10px;
  padding: 8px 12px;
  background: #0a7d5c;
  color: #fff;
  cursor: pointer;
}

.muted-text {
  color: #6e6e73;
}

.error-text {
  color: #b43c2f;
}

@media (max-width: 720px) {
  .dataset-form {
    grid-template-columns: 1fr;
  }
}
</style>
