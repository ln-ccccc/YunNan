<template>
  <section class="project-form-panel">
    <div class="panel-title-row">
      <h2>{{ editing ? '编辑项目' : '新建项目基本信息' }}</h2>
      <button class="ghost-btn" type="button" :disabled="busy" @click="$emit('cancel')">关闭</button>
    </div>

    <form class="project-form" @submit.prevent="submit">
      <label>
        项目名称
        <input v-model.trim="draft.name" required placeholder="项目名称" />
      </label>
      <label>
        区域
        <input v-model.trim="draft.region" placeholder="区域" />
      </label>
      <label>
        负责人
        <input v-model.trim="draft.manager" placeholder="负责人" />
      </label>
      <label>
        项目状态
        <select v-model="draft.status">
          <option v-for="status in lifecycleStatuses" :key="status" :value="status">
            {{ formatLifecycleStatus(status) }}
          </option>
        </select>
      </label>
      <label>
        开始年份
        <input v-model.number="draft.monitor_start_year" type="number" min="1900" max="9999" placeholder="开始年份" />
      </label>
      <label>
        结束年份
        <input v-model.number="draft.monitor_end_year" type="number" min="1900" max="9999" placeholder="结束年份" />
      </label>
      <label class="full-width">
        备注
        <textarea v-model.trim="draft.remark" placeholder="备注"></textarea>
      </label>
      <div class="form-actions full-width">
        <button class="primary-btn" type="submit" :disabled="busy">
          {{ busy ? '保存中…' : '保存项目' }}
        </button>
        <button class="secondary-btn" type="button" :disabled="busy" @click="$emit('cancel')">取消</button>
      </div>
    </form>
    <p v-if="error" class="error-text">{{ error }}</p>
  </section>
</template>

<script setup>
import { reactive, watch } from 'vue';

const props = defineProps({
  model: {
    type: Object,
    default: () => ({}),
  },
  editing: {
    type: [Boolean, Number, String],
    default: false,
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

const emit = defineEmits(['submit', 'cancel']);
const lifecycleStatuses = ['draft', 'active', 'completed', 'archived'];
const draft = reactive(createEmptyModel());

watch(
  () => props.model,
  (model) => {
    Object.assign(draft, createEmptyModel(), model || {});
    if (!lifecycleStatuses.includes(draft.status)) draft.status = 'draft';
  },
  { deep: true, immediate: true },
);

function createEmptyModel() {
  return {
    name: '',
    region: '',
    manager: '',
    remark: '',
    status: 'draft',
    monitor_start_year: '',
    monitor_end_year: '',
  };
}

function submit() {
  emit('submit', {
    name: String(draft.name || '').trim(),
    region: String(draft.region || '').trim(),
    manager: String(draft.manager || '').trim(),
    remark: String(draft.remark || '').trim(),
    status: draft.status,
    monitor_start_year: normalizeYear(draft.monitor_start_year),
    monitor_end_year: normalizeYear(draft.monitor_end_year),
  });
}

function normalizeYear(value) {
  const year = Number(value);
  return Number.isInteger(year) && year > 0 ? year : null;
}

function formatLifecycleStatus(status) {
  return {
    draft: '草稿',
    active: '进行中',
    completed: '已完成',
    archived: '已归档',
  }[status] || status || '--';
}
</script>

<style scoped>
.project-form-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.panel-title-row,
.form-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.panel-title-row {
  justify-content: space-between;
}

.panel-title-row h2 {
  margin: 0;
}

.project-form {
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

button {
  border-radius: 10px;
  padding: 8px 12px;
  cursor: pointer;
}

.primary-btn {
  border: none;
  background: #2f7a68;
  color: #fff;
}

.secondary-btn {
  border: 1px solid rgba(47, 122, 104, 0.24);
  background: #eaf6f1;
  color: #1f5c4d;
}

.ghost-btn {
  border: none;
  background: transparent;
  color: #2f7a68;
}

.error-text {
  margin: 0;
  color: #b43c2f;
}

@media (max-width: 720px) {
  .project-form {
    grid-template-columns: 1fr;
  }
}
</style>
