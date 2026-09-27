import { ref, onMounted, onUnmounted } from 'vue';

import { formatDate } from '../utils/formatDate.js';

// 本地时钟（原 useWeather 中不依赖网络的部分；天气/空气等外网能力
// 已随内网无网络部署决策整体移除——2026-09-20 用户确认）
export function useClock() {
  const currentDate = ref('');
  const currentTime = ref('');

  let timeInterval = null;

  const updateDateTime = () => {
    const now = new Date();
    currentDate.value = formatDate(now);
    currentTime.value = formatDate(now).slice(11, 16);
  };

  onMounted(() => {
    updateDateTime();
    timeInterval = setInterval(updateDateTime, 1000);
  });

  onUnmounted(() => {
    if (timeInterval) clearInterval(timeInterval);
  });

  return {
    currentDate,
    currentTime,
  };
}
