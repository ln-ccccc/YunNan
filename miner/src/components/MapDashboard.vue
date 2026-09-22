<template>
  <div class="dashboard">
    <TheHeader
      :currentDate="currentDate"
      :currentTime="currentTime"
      :username="username"
      :secondaryActionLabel="headerActionLabel"
      :projectId="projectId"
      @secondary-action="emit('back-to-projects')"
      @logout="emit('logout')"
    />

    <main class="main-container">
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
        :dataLoadError="dataLoadError"
        @apply-filters="applyFilters"
        @reset-filters="resetFilters"
        @search="performSearch"
        @open-inference="showInferenceModal = true"
        @open-trend-report="openTrendReport"
      />

      <MapContainer
        v-if="mapManifest?.tile_url"
        ref="mapContainerRef"
        :minesData="filteredMinesData"
        :tile-url="mapManifest?.tile_url || ''"
        :min-zoom="mapManifest?.min_zoom"
        :max-zoom="mapManifest?.max_zoom"
        :leftCollapsed="false"
        :rightCollapsed="false"
        @select-mine="handleSelectMine"
      />
      <div v-else class="map-unavailable">{{ dataLoadError || '项目离线地图加载中…' }}</div>

      <RightSidebar
        :treatedCount="treatedCount"
        :untreatedCount="untreatedCount"
        :landTypeList="landTypeList"
        :miningMethodList="miningMethodList"
        :changeAreaStats="changeAreaStats"
        :collapsed="rightSidebarCollapsed"
        @toggle="rightSidebarCollapsed = !rightSidebarCollapsed"
      />
    </main>

    <MineDetailModal
      :visible="showMineDetail"
      :mineData="selectedMine"
      :indicesData="mineIndices"
      :changeMatrixData="mineChangeMatrix"
      :originalImageryData="mineOriginalImagery"
      :selectedTab="selectedTab"
      :formatMaybeNumber="formatMaybeNumber"
      :formatTrend="formatTrend"
      :getTrendClass="getTrendClass"
      @close="showMineDetail = false"
      @tab-change="selectedTab = $event"
      @download-original="downloadOriginalImagery"
    />

    <InferenceModal
      :visible="showInferenceModal"
      :running="inferenceRunning"
      :error="imageryAssetsError || inferenceError"
      :result="inferenceResult"
      :imagery-assets="imageryAssets"
      :assets-loading="imageryAssetsLoading"
      @close="showInferenceModal = false"
      @load-imagery="loadInferenceImagery"
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
import { defineEmits, defineExpose, defineProps, nextTick, onMounted, onUnmounted, ref } from 'vue';
import 'leaflet/dist/leaflet.css';

import TheHeader from './TheHeader.vue';
import LeftSidebar from './LeftSidebar.vue';
import RightSidebar from './RightSidebar.vue';
import MapContainer from './MapContainer.vue';
import MineDetailModal from './MineDetailModal.vue';
import InferenceModal from './InferenceModal.vue';
import TrendReportModal from './TrendReportModal.vue';

import { useClock } from '../composables/useClock';
import { useMineData } from '../composables/useMineData';
import { createWindowResizeListener } from '../composables/windowResizeListener.js';
import { createProjectWorkspaceApi } from '../projectWorkspace/projectWorkspaceApi.js';

const props = defineProps({
  projectId: {
    type: Number,
    required: true,
  },
  headerActionLabel: {
    type: String,
    default: '',
  },
  username: {
    type: String,
    default: '',
  },
});

const emit = defineEmits(['back-to-projects', 'logout']);
const rawMinerApiBase = import.meta.env.VITE_MINER_API_BASE_URL;
const projectApi = createProjectWorkspaceApi({
  baseUrl: rawMinerApiBase ? String(rawMinerApiBase).replace(/\/$/, '') : '',
});

const showMineDetail = ref(false);
const showInferenceModal = ref(false);
const showTrendReportModal = ref(false);
const rightSidebarCollapsed = ref(false);
const selectedMine = ref({});
const selectedTab = ref('NDVI');
const mapContainerRef = ref(null);
const resizeListener = createWindowResizeListener(() => {
  mapContainerRef.value?.invalidateSize?.();
});
const imageryAssets = ref([]);
const imageryAssetsLoading = ref(false);
const imageryAssetsError = ref('');

const {
  currentDate, currentTime,
} = useClock();

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
  dataLoadError,
  mineIndices,
  mineChangeMatrix,
  mineOriginalImagery,
  downloadOriginalImagery,
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
  runProjectInference,
  trendReportLoading,
  trendReportError,
  trendReport,
  fetchTrendReport,
  exportTrendReport,
  mapManifest,
} = useMineData(() => props.projectId);

const loadInferenceImagery = async () => {
  imageryAssetsLoading.value = true;
  imageryAssetsError.value = '';
  try {
    const response = await projectApi.loadAssets(props.projectId, { type: 'imagery', status: 'ready' });
    imageryAssets.value = response?.items || [];
  } catch (error) {
    imageryAssets.value = [];
    imageryAssetsError.value = error?.message || '推理影像加载失败';
  } finally {
    imageryAssetsLoading.value = false;
  }
};

const performSearch = () => {
  if (!searchMineId.value) return;

  const keyword = searchMineId.value.toLowerCase();
  const target = allMinesData.value.find((feature) => {
    const properties = feature.properties || {};
    return (
      String(properties.FID_1) === keyword
      || String(properties.mine_name || '').toLowerCase().includes(keyword)
    );
  });

  if (!target) {
    alert('未找到对应矿山');
    return;
  }

  if (!filteredMinesData.value.find((item) => item.properties.FID_1 === target.properties.FID_1)) {
    resetFilters();
  }

  nextTick(() => {
    mapContainerRef.value?.flyToMine?.(target.properties.FID_1);
  });
};

const handleSelectMine = async ({ feature, center }) => {
  const properties = feature.properties || {};
  selectedMine.value = {
    mine_id: properties.FID_1,
    name: properties.mine_name || properties.name || `矿山 ${properties.FID_1}`,
    area: properties.area || properties.TBTYMJ,
    status_raw: properties.HFZLQK,
    status_normalized: properties.status_normalized,
    center_lat: center.lat,
    center_lng: center.lng,
  };

  showMineDetail.value = true;
  selectedTab.value = 'NDVI';

  await fetchIndices(properties.FID_1);
};

const focusByFid = (fid) => {
  searchMineId.value = String(fid);
  performSearch();
};

const handleInferenceSubmit = async (formData) => {
  if (!Number.isSafeInteger(formData?.datasetId) || formData.datasetId <= 0) return;
  try {
    const result = await runProjectInference(formData);
    const writtenFids = Array.isArray(result?.result?.written_fid_list)
      ? result.result.written_fid_list
      : [];
    if (['succeeded', 'succeeded_with_fallback', 'partial_failed'].includes(result?.status)) {
      await loadData();
      showInferenceModal.value = false;
      if (writtenFids.length > 0) focusByFid(writtenFids[0]);
    }
  } catch (_) {
    // 错误文案通过 useMineData 暴露给弹窗
  }
};

const openInferenceModal = () => {
  showInferenceModal.value = true;
};

const openTrendReport = async () => {
  showTrendReportModal.value = true;
  if (!trendReport.value && !trendReportLoading.value) {
    try {
      await fetchTrendReport({ class_name: 'bareground', direction: 'all' });
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
  } catch (_) {
    alert('导出失败，请稍后重试');
  }
};

onMounted(() => {
  loadData();
  resizeListener.attach();
});

onUnmounted(() => {
  resizeListener.detach();
});

defineExpose({
  focusByFid,
  openInferenceModal,
});
</script>

<style scoped>
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
  background:
    radial-gradient(circle at 50% 44%, rgba(36, 193, 255, 0.12), transparent 46%),
    #0a1929;
  color: #e0f7ff;
  overflow: hidden;
  font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
  display: flex;
  flex-direction: column;
}

.main-container {
  flex: 1;
  position: relative;
  display: flex;
  overflow: hidden;
  min-width: 0;
  gap: 0;
}
</style>
