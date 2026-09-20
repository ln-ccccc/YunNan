<template>
  <header class="header">
    <div class="header-left">
      <div class="logo-area">
        <h1 class="title">矿山生态修复智能监测平台</h1>
      </div>
    </div>
    <div class="header-right">
      <div class="time-widget">{{ currentDate }} {{ currentTime }}</div>
      <div class="user-profile">
        <span class="role">{{ username || '管理员' }}</span>
      </div>
      <button
        v-if="username"
        class="secondary-btn"
        type="button"
        @click="$emit('logout')"
      >
        <span>退出登录</span>
      </button>
      <button
        v-if="secondaryActionLabel"
        class="secondary-btn"
        type="button"
        @click="$emit('secondary-action')"
      >
        <span>{{ secondaryActionLabel }}</span>
      </button>
      <button class="system-btn" type="button" @click="goToGeoView">
        <span>解译平台</span>
        <svg viewBox="0 0 24 24" width="16" height="16" stroke="currentColor" stroke-width="2" fill="none">
          <path d="M5 12h14M12 5l7 7-7 7" />
        </svg>
      </button>
    </div>
  </header>
</template>

<script setup>
import { buildGeoViewUrl } from '../navigation/geoviewNavigation.js';

const props = defineProps({
  currentDate: String,
  currentTime: String,
  username: {
    type: String,
    default: '',
  },
  secondaryActionLabel: {
    type: String,
    default: '',
  },
  projectId: {
    type: Number,
    required: true,
  },
});

defineEmits(['secondary-action', 'logout']);

const goToGeoView = () => {
  const configuredUrl = import.meta.env.VITE_GEOVIEW_URL || 'http://localhost:3000/segmentation';
  window.location.href = buildGeoViewUrl(configuredUrl, window.location, props.projectId);
};
</script>

<style scoped>
.header {
  height: 60px;
  /* 同 LeftSidebar：移除 backdrop-filter 避免 GPU 合成伪影 */
  background: rgba(10, 25, 41, 0.97);
  border-bottom: 1px solid var(--border-color);
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 20px;
  z-index: 2000;
}

.header-left,
.header-right {
  display: flex;
  align-items: center;
  gap: 20px;
}

.logo-area {
  display: flex;
  align-items: center;
  gap: 10px;
}

.title {
  font-size: 20px;
  font-weight: 600;
  letter-spacing: 0;
  margin: 0;
  background: linear-gradient(90deg, #fff, #4ecdc4);
  -webkit-background-clip: text;
  color: transparent;
}

.secondary-btn,
.system-btn {
  border: none;
  padding: 8px 16px;
  border-radius: 4px;
  color: #fff;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 500;
  transition: transform 0.2s, box-shadow 0.2s;
}

.secondary-btn {
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.16);
}

.system-btn {
  background: linear-gradient(135deg, #0984e3, #00cec9);
}

.secondary-btn:hover,
.system-btn:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(9, 132, 227, 0.25);
}
</style>
