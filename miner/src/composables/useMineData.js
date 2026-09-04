import { ref, unref } from 'vue';
import axios from 'axios';
import { pollInferenceJob } from '../services/inferencePolling.js';

const rawMinerApiBase = import.meta.env.VITE_MINER_API_BASE_URL;
const MINER_API_BASE_URL = rawMinerApiBase ? String(rawMinerApiBase).replace(/\/$/, '') : '';
const apiUrl = (path) => `${MINER_API_BASE_URL}${path}`;
const buildEmptyIndexEntry = () => ({
  mean: 0,
  trend: 0,
  mk_trend: '暂无',
  data: [],
  available: false,
  message: '暂无项目数据',
  reason: 'missing_project_data',
  source_file: '',
});

export function useMineData(projectId) {
  const resolveProjectId = () => Number(typeof projectId === 'function' ? projectId() : unref(projectId));
  const projectApiPath = (suffix) => {
    const value = resolveProjectId();
    if (!Number.isSafeInteger(value) || value <= 0) throw new Error('缺少有效项目 ID');
    return `/api/projects/${value}${suffix}`;
  };
  const unwrap = (response) => response?.data?.data ?? response?.data ?? null;
  const allMinesData = ref([]);
  const filteredMinesData = ref([]);

  const filterCity = ref('');
  const filterStatus = ref('');
  const filterMethod = ref('');
  const searchMineId = ref('');

  const cityOptions = ref([]);
  const miningMethodOptions = ref([]);

  const mineTotal = ref(0);
  const overviewArea = ref(0);
  const treatedCount = ref(0);
  const untreatedCount = ref(0);
  const restorationMethodList = ref([]);
  const miningMethodList = ref([]);
  const landTypeList = ref([]);
  const changeAreaStats = ref({
    total_changed_km2: 0,
    valid_mine_count: 0,
    missing_mine_count: 0,
    coverage_ratio: 0
  });
  const mineChangeAreaList = ref([]);

  const mineIndices = ref({
    ndvi: buildEmptyIndexEntry(),
    ndbi: buildEmptyIndexEntry(),
    ndwi: buildEmptyIndexEntry(),
    ndsi: buildEmptyIndexEntry()
  });

  const mineChangeMatrix = ref(null);
  const mineChangeDetails = ref(null);
  const inferenceRunning = ref(false);
  const inferenceResult = ref(null);
  const inferenceError = ref('');
  const dataLoadError = ref('');
  const trendReportLoading = ref(false);
  const trendReportError = ref('');
  const trendReport = ref(null);
  const mapManifest = ref(null);

  const loadData = async () => {
    dataLoadError.value = '';
    allMinesData.value = [];
    filteredMinesData.value = [];
    cityOptions.value = [];
    miningMethodOptions.value = [];
    mineTotal.value = 0;
    mapManifest.value = null;
    try {
      const [manifestRes, statsRes, geoRes] = await Promise.all([
        axios.get(apiUrl(projectApiPath('/map/manifest'))),
        axios.get(apiUrl(projectApiPath('/stats'))),
        axios.get(apiUrl(projectApiPath('/geojson'))),
      ]);
      mapManifest.value = unwrap(manifestRes);
      const stats = unwrap(statsRes) || {};
      mineTotal.value = stats.mineTotal;
      overviewArea.value = stats.mineAreaTotal;
      treatedCount.value = stats.treatedCount;
      untreatedCount.value = stats.untreatedCount;
      restorationMethodList.value = stats.restorationMethodList || [];
      miningMethodList.value = stats.miningMethodList || [];
      landTypeList.value = stats.landTypeList || [];
      changeAreaStats.value = stats.changeAreaStats || changeAreaStats.value;
      mineChangeAreaList.value = stats.mineChangeAreaList || [];

      const geojson = unwrap(geoRes);
      if (geojson && geojson.features) {
        allMinesData.value = geojson.features;

        const cities = new Set();
        const methods = new Set();
        allMinesData.value.forEach((f) => {
          const p = f.properties || {};
          if (p.SHI) cities.add(p.SHI);
          if (p.KCFS) methods.add(p.KCFS);
        });
        cityOptions.value = Array.from(cities).filter(Boolean);
        miningMethodOptions.value = Array.from(methods).filter(Boolean);

        applyFilters();
      }
    } catch (e) {
      console.error('Data load error:', e);
      dataLoadError.value = e?.response?.data?.error || e?.message || '矿山数据加载失败，请检查 Miner API 服务';
    }
  };

  const applyFilters = () => {
    filteredMinesData.value = allMinesData.value.filter((f) => {
      const p = f.properties || {};
      const cityMatch = !filterCity.value || p.SHI === filterCity.value;
      const methodMatch = !filterMethod.value || p.KCFS === filterMethod.value;

      let statusMatch = true;
      if (filterStatus.value) {
        const norm = p.status_normalized || 'unknown';
        statusMatch = norm === filterStatus.value;
      }

      let searchMatch = true;
      if (searchMineId.value) {
        const q = searchMineId.value.toLowerCase();
        const idMatch = String(p.FID_1) === q;
        const nameMatch = p.mine_name && p.mine_name.includes(q);
        searchMatch = idMatch || nameMatch;
      }

      return cityMatch && methodMatch && statusMatch && (searchMineId.value ? searchMatch : true);
    });
  };

  const resetFilters = () => {
    filterCity.value = '';
    filterStatus.value = '';
    filterMethod.value = '';
    searchMineId.value = '';
    applyFilters();
  };

  const fetchIndices = async (fid) => {
    try {
      const res = await axios.get(apiUrl(projectApiPath(`/mines/indices?fid=${encodeURIComponent(fid)}`)));
      const merged = unwrap(res) || {};
      mineIndices.value = merged.available === false
        ? { ndvi: buildEmptyIndexEntry(), ndbi: buildEmptyIndexEntry(), ndwi: buildEmptyIndexEntry(), ndsi: buildEmptyIndexEntry() }
        : merged;
      await fetchChangeMatrix(fid);
    } catch (e) {
      console.warn('No indices data for FID:', fid);
      mineIndices.value = {
        ndvi: buildEmptyIndexEntry(),
        ndbi: buildEmptyIndexEntry(),
        ndwi: buildEmptyIndexEntry(),
        ndsi: buildEmptyIndexEntry(),
      };
      mineChangeMatrix.value = null;
    }
  };

  const fetchChangeMatrix = async (fid) => {
    try {
      const res = await axios.get(apiUrl(projectApiPath(`/mines/change-matrix?fid=${encodeURIComponent(fid)}`)));
      const data = unwrap(res) || {};
      mineChangeMatrix.value = { ...data, has_change_matrix: Boolean(data.has_change_matrix), data_source: data.data_source || 'project_output' };
    } catch (e) {
      mineChangeMatrix.value = {
        fid: Number(fid),
        headers: [],
        matrix: [],
        images: { old: null, new: null },
        has_change_matrix: false,
        data_source: 'none'
      };
    }
  };

  const fetchTrendReport = async (filters = {}) => {
    trendReportLoading.value = true;
    trendReportError.value = '';
    try {
      const class_name = filters.class_name || 'bareground';
      const direction = filters.direction || 'all';
      const res = await axios.get(apiUrl(projectApiPath('/mines/trend-report')), {
        params: { class_name, direction }
      });
      trendReport.value = unwrap(res) || null;
      return trendReport.value;
    } catch (e) {
      trendReportError.value = e?.response?.data?.error || e?.message || '趋势统计加载失败';
      throw e;
    } finally {
      trendReportLoading.value = false;
    }
  };

  const exportTrendReport = async () => {
    const rows = trendReport.value?.tables?.selected_class_rows || [];
    const header = ['FID', 'MineName', 'StartYear', 'EndYear', 'StartPercent', 'EndPercent', 'DeltaPercent', 'StartAreaKm2', 'EndAreaKm2', 'DeltaAreaKm2'];
    const lines = [header.join(',')];
    rows.forEach((r) => {
      lines.push([
        r.fid ?? '',
        `"${(r.mine_name || '').replace(/"/g, '""')}"`,
        r.start_year ?? '',
        r.end_year ?? '',
        r.start_percent ?? '',
        r.end_percent ?? '',
        r.delta_percent ?? '',
        r.start_area_km2 ?? '',
        r.end_area_km2 ?? '',
        r.delta_area_km2 ?? ''
      ].join(','));
    });
    const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `trend_report_${Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const deleteMines = async () => {
    throw new Error('当前 miner 后端不支持删除矿山');
  };

  const runKmlRoiInference = async ({
    oldTifPath,
    newTifPath,
    kmlPath = '',
    device = 'auto',
    limit = 0,
    year = '',
    oldYear = '',
    newYear = ''
  } = {}) => {
    inferenceRunning.value = true;
    inferenceError.value = '';
    inferenceResult.value = null;
    try {
      const payload = {
        project_id: resolveProjectId(),
        old_tif_path: oldTifPath,
        new_tif_path: newTifPath || oldTifPath,
        device,
        limit
      };
      if (kmlPath) payload.kml_path = kmlPath;
      if (year) payload.year = year;
      if (oldYear) payload.old_year = oldYear;
      if (newYear) payload.new_year = newYear;
      const res = await axios.post(apiUrl('/api/inference/kml-roi'), payload);
      const createdJob = res?.data?.data;
      if (!createdJob?.id) throw new Error('推理任务创建后未返回任务编号');
      inferenceResult.value = createdJob;
      const terminalJob = await pollInferenceJob({
        jobId: createdJob.id,
        getJob: async (jobId) => {
          const jobRes = await axios.get(apiUrl(`/api/inference/jobs/${encodeURIComponent(jobId)}`));
          return jobRes?.data?.data;
        },
        onUpdate: (job) => {
          inferenceResult.value = job;
        },
      });
      if (terminalJob.status === 'failed' || terminalJob.status === 'cancelled') {
        throw new Error(terminalJob?.error?.message || `推理任务${terminalJob.status === 'cancelled' ? '已取消' : '失败'}`);
      }
      return terminalJob;
    } catch (e) {
      inferenceError.value = e?.response?.data?.msg || e?.response?.data?.error || e?.message || '推理任务执行失败';
      throw e;
    } finally {
      inferenceRunning.value = false;
    }
  };

  const formatMaybeNumber = (v, d = 2) => {
    const n = Number(v);
    return Number.isFinite(n) ? n.toFixed(d) : '--';
  };

  const formatTrend = (v) => {
    const n = Number(v);
    if (!Number.isFinite(n)) return '--';
    return n > 0 ? `+${n.toFixed(4)}` : `${n.toFixed(4)}`;
  };

  const getTrendClass = (v) => {
    const n = Number(v);
    if (!Number.isFinite(n)) return '';
    return n > 0 ? 'text-green' : (n < 0 ? 'text-red' : '');
  };

  return {
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
    mineChangeAreaList,
    mineIndices,
    mineChangeMatrix,
    mineChangeDetails,
    dataLoadError,
    inferenceRunning,
    inferenceResult,
    inferenceError,
    trendReportLoading,
    trendReportError,
    trendReport,
    mapManifest,
    loadData,
    applyFilters,
    resetFilters,
    fetchIndices,
    fetchChangeMatrix,
    runKmlRoiInference,
    fetchTrendReport,
    exportTrendReport,
    deleteMines,
    formatMaybeNumber,
    formatTrend,
    getTrendClass
  };
}
