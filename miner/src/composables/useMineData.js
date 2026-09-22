import { ref, unref, onScopeDispose } from 'vue';
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
  const mineOriginalImagery = ref(null);
    const mineTraceability = ref(null);
  // fetchIndices/fetchChangeMatrix 的竞态序号：过期响应直接丢弃
  let fetchIndicesSeq = 0;
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
    // 竞态门控：快速连续点击两座矿山时，后到的旧响应不得覆盖新矿山指标
    const seq = (fetchIndicesSeq = (fetchIndicesSeq || 0) + 1);
    try {
      const res = await axios.get(apiUrl(projectApiPath(`/mines/indices?fid=${encodeURIComponent(fid)}`)));
      if (seq !== fetchIndicesSeq) return;
      const merged = unwrap(res) || {};
      mineIndices.value = merged.available === false
        ? { ndvi: buildEmptyIndexEntry(), ndbi: buildEmptyIndexEntry(), ndwi: buildEmptyIndexEntry(), ndsi: buildEmptyIndexEntry() }
        : merged;
      await fetchChangeMatrix(fid, seq);
    } catch (e) {
      if (seq !== fetchIndicesSeq) return;
      console.warn('No indices data for FID:', fid);
      mineIndices.value = {
        ndvi: buildEmptyIndexEntry(),
        ndbi: buildEmptyIndexEntry(),
        ndwi: buildEmptyIndexEntry(),
        ndsi: buildEmptyIndexEntry(),
      };
      mineChangeMatrix.value = null;
      // 失败路径此前不清原始影像：矿山 B 的弹窗会残留矿山 A 的溯源列表
      mineOriginalImagery.value = null;
      mineTraceability.value = null;
      await fetchOriginalImagery(fid, seq);
    }
  };

  const fetchChangeMatrix = async (fid, seq = fetchIndicesSeq) => {
    try {
      const res = await axios.get(apiUrl(projectApiPath(`/mines/change-matrix?fid=${encodeURIComponent(fid)}`)));
      if (seq !== fetchIndicesSeq) return;
      const data = unwrap(res) || {};
      mineChangeMatrix.value = { ...data, has_change_matrix: Boolean(data.has_change_matrix), data_source: data.data_source || 'project_output' };
    } catch (e) {
      if (seq !== fetchIndicesSeq) return;
      mineChangeMatrix.value = {
        fid: Number(fid),
        headers: [],
        matrix: [],
        images: { old: null, new: null },
        has_change_matrix: false,
        data_source: 'none'
      };
    }
    await fetchOriginalImagery(fid, seq);
    await fetchTraceability(fid, seq);
  };

  // 图斑溯源聚合（M3）：历年成果/占比/修订一次拉取，驱动详情弹窗"地物分类"溯源视图
  const fetchTraceability = async (fid, seq = fetchIndicesSeq) => {
    try {
      const res = await axios.get(apiUrl(projectApiPath(`/mines/${encodeURIComponent(fid)}/traceability`)));
      if (seq !== fetchIndicesSeq) return;
      mineTraceability.value = unwrap(res) || { fid: Number(fid), years: [] };
    } catch (e) {
      if (seq !== fetchIndicesSeq) return;
      mineTraceability.value = { fid: Number(fid), years: [] };
    }
  };

  // 地物分类原始影像（溯源）：与变化矩阵同竞态门控，列表轻量随详情一并拉取
  const fetchOriginalImagery = async (fid, seq = fetchIndicesSeq) => {
    try {
      const res = await axios.get(apiUrl(projectApiPath(`/mines/original-imagery?fid=${encodeURIComponent(fid)}`)));
      if (seq !== fetchIndicesSeq) return;
      mineOriginalImagery.value = unwrap(res) || { fid: Number(fid), items: [] };
    } catch (e) {
      if (seq !== fetchIndicesSeq) return;
      mineOriginalImagery.value = { fid: Number(fid), items: [] };
    }
  };

  // 溯源下载：经 BFF 流式转发，blob 落盘；上游 502/错误体是 JSON 时给出提示
  const downloadOriginalImagery = async (item) => {
    const jobId = item?.jobId;
    if (!jobId) return;
    try {
      const res = await axios.get(
        apiUrl(projectApiPath(`/mines/original-imagery/${encodeURIComponent(jobId)}/download`)),
        { responseType: 'blob' },
      );
      const contentType = String(res?.headers?.['content-type'] || '');
      if (contentType.includes('application/json')) {
        const text = await (res.data instanceof Blob ? res.data.text() : Promise.resolve(String(res.data)));
        let msg = '原始影像下载失败';
        try { msg = JSON.parse(text).msg || msg; } catch (_) {}
        window.alert(msg);
        return;
      }
      const blob = res.data instanceof Blob ? res.data : new Blob([res.data]);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = item.filename || `original_${jobId}.tif`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      // 同步 revoke 在部分浏览器会截断尚未开始的 blob 下载，延迟回收（审查 P3）
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (e) {
      console.warn('原始影像下载失败:', e);
      // 上游 404/502 经 axios reject 走到这里：error body 是 JSON blob，
      // 解析出后端具体文案（任务不存在/文件不存在）而非泛化提示（审查 F3）
      try {
        const errBlob = e?.response?.data;
        if (errBlob instanceof Blob) {
          const text = await errBlob.text();
          const msg = JSON.parse(text).msg;
          if (msg) { window.alert(msg); return; }
        }
      } catch (_) {}
      window.alert(e?.response?.data?.msg || '原始影像下载失败，请稍后重试');
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

  // 推理轮询取消位：App.vue 按 selectedProjectId 用 :key 重建组件，
  // 旧实例的 while 轮询若无取消会以 1s/次无限打后端
  let inferencePollCancelled = false;
  onScopeDispose(() => {
    inferencePollCancelled = true;
  });

  const runProjectInference = async ({ datasetId, year = '', device = 'auto' } = {}) => {
    inferenceRunning.value = true;
    inferenceError.value = '';
    inferenceResult.value = null;
    inferencePollCancelled = false;
    try {
      const payload = {
        project_id: resolveProjectId(),
        dataset_id: Number(datasetId),
        device,
      };
      if (year) payload.year = year;
      const res = await axios.post(apiUrl('/api/inference/jobs'), payload);
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
        isCancelled: () => inferencePollCancelled,
      });
      if (terminalJob.status === 'failed' || terminalJob.status === 'cancelled') {
        throw new Error(terminalJob?.error?.message || `推理任务${terminalJob.status === 'cancelled' ? '已取消' : '失败'}`);
      }
      return terminalJob;
    } catch (e) {
      if (e?.cancelled) {
        // 组件已销毁：不再写状态，静默退出轮询链
        throw e;
      }
      inferenceError.value = e?.response?.data?.msg || e?.response?.data?.error || e?.message || '推理任务执行失败';
      throw e;
    } finally {
      if (!inferencePollCancelled) {
        inferenceRunning.value = false;
      }
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
    mineOriginalImagery,
    mineTraceability,
    downloadOriginalImagery,
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
    runProjectInference,
    fetchTrendReport,
    exportTrendReport,
    formatMaybeNumber,
    formatTrend,
    getTrendClass
  };
}
