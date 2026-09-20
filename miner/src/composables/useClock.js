import { ref, onMounted, onUnmounted } from 'vue';

// 本地时钟（原 useWeather 中不依赖网络的部分；天气/空气等外网能力
// 已随内网无网络部署决策整体移除——2026-09-20 用户确认）
export function useClock() {
  const currentDate = ref('');
  const currentTime = ref('');

  let timeInterval = null;

  const updateDateTime = () => {
    const now = new Date();
    currentDate.value = now.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' });
    currentTime.value = now.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
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
