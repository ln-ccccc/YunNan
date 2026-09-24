<template>
  <div class="placeholder-view">
    <div class="placeholder-card">
      <h3>{{ label }}</h3>
      <p>该模块规划于 {{ milestone }} 交付，当前先占位保证导航完整。</p>
      <button class="back-btn" @click="$emit('go-projects')">返回项目管理</button>
    </div>
  </div>
</template>

<script setup>
defineProps({
  label: { type: String, required: true },
  milestone: { type: String, default: '后续里程碑' },
});
defineEmits(['go-projects']);
</script>

<style scoped>
.placeholder-view {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(180deg, #eef5f3 0%, #dce8e3 100%);
}

.placeholder-card {
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid rgba(38, 75, 69, 0.18);
  border-radius: 12px;
  padding: 40px 48px;
  text-align: center;
  color: #264b45;
}

.placeholder-card h3 { margin: 0 0 12px; font-size: 20px; }
.placeholder-card p { margin: 0 0 20px; color: #5a7d75; font-size: 14px; }

.back-btn {
  background: #2f6f61;
  color: #fff;
  border: none;
  border-radius: 6px;
  padding: 8px 22px;
  cursor: pointer;
  font-size: 14px;
}
.back-btn:hover { background: #264b45; }
</style>
