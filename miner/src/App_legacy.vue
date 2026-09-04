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
    <section v-if="showInferencePanel" class="inference-panel">
      <h3>KML ROI Inference</h3>
      <div class="row">
        <input v-model="oldTifPath" placeholder="Old tif path (server path)" />
      </div>
      <div class="row">
        <input v-model="newTifPath" placeholder="New tif path (optional, default old)" />
      </div>
      <div class="row compact">
        <input v-model="kmlPath" placeholder="KML path (optional)" />
        <input v-model="inferenceDevice" placeholder="cpu/cuda:0" />
        <input v-model.number="inferenceLimit" type="number" min="0" placeholder="limit" />
      </div>
      <div class="row">
        <button :disabled="inferenceRunning" @click="runInference">{{ inferenceRunning ? 'Running...' : 'Run Inference' }}</button>
      </div>
      <div v-if="inferenceError" class="error">{{ inferenceError }}</div>
      <div v-if="inferenceResult" class="result">
        <div>Matched: {{ inferenceResult.matched_fids || 0 }}, Written: {{ inferenceResult.written_fids || 0 }}</div>
        <div class="fid-list">
          <button v-for="fid in (inferenceResult.written_fid_list || [])" :key="fid" @click="focusByFid(fid)">FID {{ fid }}</button>
        </div>
      </div>
    </section>
    <!-- 主体内容 -->
    <main class="main-container">
      <!-- 左侧边栏 -->
      <LeftSidebar
        v-model:filterCity="filterCity"
        v-model:filterStatus="filterStatus"
        v-model:filterMethod="filterMethod"
        v-model:searchMineId="searchMineId"
        :collapsed="leftCollapsed"
        :mineTotal="mineTotal"
        :overviewArea="overviewArea"
        :treatedCount="treatedCount"
        :untreatedCount="untreatedCount"
        :restorationMethodList="restorationMethodList"
        :cityOptions="cityOptions"
        :miningMethodOptions="miningMethodOptions"
        @toggle="leftCollapsed = !leftCollapsed"
        @apply-filters="applyFilters"
        @reset-filters="resetFilters"
        @search="performSearch"
      />

      <!-- 中间地图区域 -->
      <MapContainer
        ref="mapContainerRef"
        :minesData="filteredMinesData"
        :leftCollapsed="leftCollapsed"
        :rightCollapsed="rightCollapsed"
        @select-mine="handleSelectMine"
      />

      <!-- 右侧边栏 -->
      <RightSidebar
        :collapsed="rightCollapsed"
        :treatedCount="treatedCount"
        :untreatedCount="untreatedCount"
        :landTypeList="landTypeList"
        :miningMethodList="miningMethodList"
        :changeAreaStats="changeAreaStats" 
        @toggle="rightCollapsed = !rightCollapsed"
      />
    </main>

    <!-- 矿山详情弹窗 -->
    <MineDetailModal
      :visible="showMineDetail"
      :mineData="selectedMine"
      :indicesData="mineIndices"
      :changeMatrixData="mineChangeMatrix"
      :selectedTab="selectedTab"
      :formatMaybeNumber="formatMaybeNumber"
      :formatTrend="formatTrend"
      :getTrendClass="getTrendClass"
      @close="showMineDetail = false"
      @tab-change="selectedTab = $event"
    />
  </div>
</template>

<script setup>
import { onMounted, ref, nextTick } from 'vue';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

// Components
import TheHeader from './components/TheHeader.vue';
import LeftSidebar from './components/LeftSidebar.vue';
import RightSidebar from './components/RightSidebar.vue';
import MapContainer from './components/MapContainer.vue';
import MineDetailModal from './components/MineDetailModal.vue';

// Composables
import { useWeather } from './composables/useWeather';
import { useMineData } from './composables/useMineData';

// --- State ---
const leftCollapsed = ref(false);
const rightCollapsed = ref(false);
const showMineDetail = ref(false);
const selectedMine = ref({});
const selectedTab = ref('NDVI');

const mapContainerRef = ref(null);
const oldTifPath = ref('/app/backend/bianhua_2years/mine_TEST.tif');
const newTifPath = ref('/app/backend/bianhua_2years/mine_TEST.tif');
const kmlPath = ref('');
const inferenceDevice = ref('cpu');
const inferenceLimit = ref(0);
const showInferencePanel = ref(false);

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
  changeAreaStats,
  mineIndices,
  mineChangeMatrix,
  loadData,
  applyFilters,
  resetFilters,
  fetchIndices,
  formatMaybeNumber,
  formatTrend,
  getTrendClass,
  inferenceRunning,
  inferenceResult,
  inferenceError,
  runKmlRoiInference
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


const focusByFid = (fid) => {
  searchMineId.value = String(fid);
  performSearch();
};

const runInference = async () => {
  if (!oldTifPath.value) {
    alert('������дӰ��·��');
    return;
  }
  try {
    const result = await runKmlRoiInference({
      oldTifPath: oldTifPath.value,
      newTifPath: newTifPath.value || oldTifPath.value,
      kmlPath: kmlPath.value,
      device: inferenceDevice.value || 'cpu',
      limit: Number(inferenceLimit.value || 0)
    });
    if (result && Array.isArray(result.written_fid_list) && result.written_fid_list.length > 0) {
      focusByFid(result.written_fid_list[0]);
    }
  } catch (_) {
    // error text already shown by composable
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

.inference-panel {
  position: absolute;
  top: 70px;
  left: 14px;
  z-index: 1000;
  width: 520px;
  max-width: calc(100vw - 40px);
  background: rgba(10, 20, 30, 0.88);
  border: 1px solid rgba(100, 180, 220, 0.35);
  border-radius: 8px;
  padding: 10px;
}
.inference-panel h3 {
  margin: 0 0 8px 0;
  font-size: 14px;
}
.inference-panel .row {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}
.inference-panel .row.compact input:nth-child(1) {
  flex: 1;
}
.inference-panel input {
  flex: 1;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.15);
  color: #e8f6ff;
  border-radius: 4px;
  padding: 6px 8px;
}
.inference-panel button {
  background: #2f9cff;
  color: #fff;
  border: none;
  border-radius: 4px;
  padding: 6px 10px;
  cursor: pointer;
}
.inference-panel button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.inference-panel .error {
  color: #ff7f7f;
  font-size: 12px;
}
.inference-panel .result {
  font-size: 12px;
}
.inference-panel .fid-list {
  margin-top: 6px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
</style>




