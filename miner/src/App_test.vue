<template>
  <div class="dashboard">
    <!-- 顶部导航�?-->
    <TheHeader 
      :weatherIcon="weatherIcon"
      :temperature="temperature"
      :airQuality="airQuality"
      :currentDate="currentDate"
      :currentTime="currentTime"
      :getAqiClass="getAqiClass"
    />
    
    <!-- 主体内容 -->
    <main class="main-container">
      <!-- 左侧边栏 -->
      <LeftSidebar
        v-model:filterCity="filterCity"
        v-model:filterStatus="filterStatus"
        v-model:filterMethod="filterMethod"
        v-model:searchMineId="searchMineId"
        :mineTotal="mineTotal"
        :overviewArea="overviewArea"
        :treatedCount="treatedCount"
        :untreatedCount="untreatedCount"
        :restorationMethodList="restorationMethodList"
        :cityOptions="cityOptions"
        :miningMethodOptions="miningMethodOptions"
        @apply-filters="applyFilters"
        @reset-filters="resetFilters"
        @search="performSearch"
        @open-inference="showInferenceModal = true"
        @open-trend-report="openTrendReport"
      />

      <!-- 中间地图区域 -->
      <MapContainer
        ref="mapContainerRef"
        :minesData="filteredMinesData"
        :leftCollapsed="false"
        :rightCollapsed="false"
        @select-mine="handleSelectMine"
      />

      <!-- 右侧边栏 -->
      <RightSidebar
        :treatedCount="treatedCount"
        :untreatedCount="untreatedCount"
        :landTypeList="landTypeList"
        :miningMethodList="miningMethodList" 
      />
    </main>

    <!-- 矿山详情弹窗 -->
    <MineDetailModal
      :visible="showMineDetail"
      :mineData="selectedMine"
      :indicesData="mineIndices"
      :changeMatrixData="mineChangeMatrix"
        :changeDetailsData="mineChangeDetails"
      :selectedTab="selectedTab"
      :formatMaybeNumber="formatMaybeNumber"
      :formatTrend="formatTrend"
      :getTrendClass="getTrendClass"
      @close="showMineDetail = false"
      @tab-change="selectedTab = $event"
      @delete="handleDeleteMine"
    />

    <InferenceModal
      :visible="showInferenceModal"
      :running="inferenceRunning"
      :error="inferenceError"
      :result="inferenceResult"
      @close="showInferenceModal = false"
      @submit="handleInferenceSubmit"
    />

    <TrendReportModal
      :visible="showTrendReportModal"
      :loading="trendReportLoading"
      :error="trendReportError"
      :report="trendReport"
      @close="showTrendReportModal = false"
      @refresh="refreshTrendReport"
      @export="handleExportTrendReport"
    />
  </div>
</template>

<script setup>
import { onMounted, ref, nextTick } from 'vue';
import 'leaflet/dist/leaflet.css';

// Components
import TheHeader from './components/TheHeader.vue';
import LeftSidebar from './components/LeftSidebar.vue';
import RightSidebar from './components/RightSidebar.vue';
import MapContainer from './components/MapContainer.vue';
import MineDetailModal from './components/MineDetailModal.vue';
import InferenceModal from './components/InferenceModal.vue';
import TrendReportModal from './components/TrendReportModal.vue';

// Composables
import { useWeather } from './composables/useWeather';
import { useMineData } from './composables/useMineData';

// --- State ---
const showMineDetail = ref(false);
const showInferenceModal = ref(false);
const showTrendReportModal = ref(false);
const selectedMine = ref({});
const selectedTab = ref('NDVI');

const mapContainerRef = ref(null);

// --- Composables Usage ---
const { 
  currentDate, currentTime, temperature, weatherIcon, airQuality, getAqiClass, fetchRealtimeEnvironmentAt 
} = useWeather();

const {
  allMinesData,
  filteredMinesData,
  filterCity,
  filterStatus,
  filterMethod,
  searchMineId,
  cityOptions,
  miningMethodOptions,
  mineTotal,
  overviewArea,
  treatedCount,
  untreatedCount,
  restorationMethodList,
  miningMethodList,
  landTypeList,
  mineIndices,
  mineChangeMatrix,
  mineChangeDetails,
  loadData,
  applyFilters,
  resetFilters,
  fetchIndices,
  formatMaybeNumber,
  formatTrend,
  getTrendClass,
  deleteMines,
  inferenceRunning,
  inferenceResult,
  inferenceError,
  runKmlRoiInference,
  trendReportLoading,
  trendReportError,
  trendReport,
  fetchTrendReport,
  exportTrendReport
} = useMineData();

// --- Event Handlers ---

const performSearch = () => {
  if (!searchMineId.value) return;
  
  // Find target in all data
  const target = allMinesData.value.find(f => {
    const p = f.properties;
    const q = searchMineId.value.toLowerCase();
    return String(p.FID_1) === q || (p.mine_name && p.mine_name.includes(q));
  });

  if (target) {
     // If filtered out, reset filters or ensure it is visible?
     // We can try to fly to it if it exists in filteredMinesData, otherwise we might need to reset.
     // Let's reset filters to be safe so it appears on map.
     if (!filteredMinesData.value.find(f => f.properties.FID_1 === target.properties.FID_1)) {
       resetFilters();
     }
     
     // Need to wait for map to re-render with new data
     nextTick(() => {
        if (mapContainerRef.value) {
          mapContainerRef.value.flyToMine(target.properties.FID_1);
        }
     });
  } else {
    alert('未找到该矿山');
  }
};

const handleSelectMine = async ({ feature, center }) => {
  const p = feature.properties;
  selectedMine.value = {
    mine_id: p.FID_1,
    name: p.mine_name || p.name || `矿山 ${p.FID_1}`,
    area: p.area || p.TBTYMJ,
    status_raw: p.HFZLQK,
    status_normalized: p.status_normalized,
    center_lat: center.lat,
    center_lng: center.lng
  };
  
  showMineDetail.value = true;
  selectedTab.value = 'NDVI';
  
  await fetchIndices(p.FID_1);
  
  // Update weather for this location
  fetchRealtimeEnvironmentAt(center.lat, center.lng);
};

const handleDeleteMine = async (fid) => {
  const fidNum = Number(fid);
  if (!Number.isFinite(fidNum)) return;
  const ok = window.confirm(`确认删除矿山 ${fidNum}？删除后将从列表与地图中隐藏，可通过恢复接口恢复。`);
  if (!ok) return;
  try {
    await deleteMines(fidNum);
    showMineDetail.value = false;
    selectedMine.value = {};
    await loadData();
  } catch (e) {
    alert(`删除失败：${e?.response?.data?.error || e?.message || 'unknown error'}`);
  }
};


const focusByFid = (fid) => {
  searchMineId.value = String(fid);
  performSearch();
};

const handleInferenceSubmit = async (formData) => {
  if (!formData.oldTifPath) return;
  try {
    const result = await runKmlRoiInference({
      oldTifPath: formData.oldTifPath,
      newTifPath: formData.newTifPath || formData.oldTifPath,
      kmlPath: formData.kmlPath,
      year: formData.singleYear,
      oldYear: formData.oldYear,
      newYear: formData.newYear,
      device: formData.device || 'auto',
      limit: 0,
      syncIndices: true,
      indexTypes: ['ndvi', 'ndbi', 'ndwi', 'ndsi']
    });
    const writtenCount = Array.isArray(result?.written_fid_list) ? result.written_fid_list.length : 0;
    const kmlChangedCount = Number(result?.kml_update?.updated || 0) + Number(result?.kml_update?.inserted || 0);
    if (writtenCount > 0 || kmlChangedCount > 0) {
      // Reload geodata to ensure updated/new polygons are rendered on the map.
      await loadData();
    }
    if (writtenCount > 0) {
      showInferenceModal.value = false;
      focusByFid(result.written_fid_list[0]);
    } else if (kmlChangedCount > 0) {
      showInferenceModal.value = false;
    }
  } catch (e) {
    // error handled by composable
  }
};

const openTrendReport = async () => {
  showTrendReportModal.value = true;
  if (!trendReport.value && !trendReportLoading.value) {
    try {
      await fetchTrendReport({ class_name: 'forest', direction: 'upward' });
    } catch (_) {}
  }
};

const refreshTrendReport = async (filters = {}) => {
  try {
    await fetchTrendReport(filters);
  } catch (_) {}
};

const handleExportTrendReport = async (filters = {}) => {
  try {
    await exportTrendReport(filters);
  } catch (e) {
    alert('导出失败，请稍后重试');
  }
};
// --- Lifecycle ---
onMounted(() => {
  loadData();
  
  // Initial weather for a default center (e.g. Dali)
  fetchRealtimeEnvironmentAt(25.6, 100.2);
  
  window.addEventListener('resize', () => {
    if (mapContainerRef.value) mapContainerRef.value.invalidateSize();
  });
});
</script>

<style scoped>
/* 基础变量 */
:root {
  --bg-dark: #0a1929;
  --panel-bg: rgba(13, 27, 42, 0.75);
  --border-color: rgba(78, 205, 196, 0.3);
  --text-primary: #e0f7ff;
  --text-secondary: #8da3b6;
  --accent-cyan: #4ecdc4;
  --accent-blue: #24c1ff;
}

.dashboard {
  width: 100vw;
  height: 100vh;
  background-color: #0a1929;
  color: #e0f7ff;
  overflow: hidden;
  font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
  display: flex;
  flex-direction: column;
}

/* Main Layout */
.main-container {
  flex: 1;
  position: relative;
  display: flex;
  overflow: hidden;
}
</style>




