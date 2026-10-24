<template>
  <div class="imagery-view">
    <header class="view-header">
      <div>
        <p class="kicker">影像管理</p>
        <h1>影像工作台</h1>
        <p class="muted-text">影像清单 · 范围裁剪（手绘/矿山边界+外扩） · 自动切片（固定像素/面积）</p>
      </div>
      <div class="header-actions">
        <button class="primary-btn" type="button" :disabled="!selectedProjectId" @click="goWorkspace">项目工作台（上传/KML）</button>
      </div>
    </header>

    <section class="view-body">
      <div class="row">
        <label for="imagery-project">项目</label>
        <select id="imagery-project" v-model="selectedProjectId" @change="loadCandidates">
          <option :value="null" disabled>请选择项目</option>
          <option v-for="project in projects" :key="project.id" :value="project.id">{{ project.name }}</option>
        </select>
        <button class="secondary-btn" type="button" :disabled="!selectedProjectId || candidatesLoading" @click="loadCandidates">刷新清单</button>
      </div>

      <p v-if="candidatesLoading" class="empty-block">影像清单加载中…</p>
      <p v-else-if="error" class="error-text">{{ error }}</p>
      <p v-else-if="!selectedProjectId" class="empty-block">选择项目后可查看影像并使用裁剪/切片工具</p>
      <template v-else>
        <h3>影像清单（{{ candidates.length }}）</h3>
        <div class="candidate-table">
          <div class="candidate-row head">
            <span>名称</span><span>尺寸</span><span>波段</span><span>大小</span><span></span>
          </div>
          <div
            v-for="item in candidates"
            :key="item.key"
            class="candidate-row"
            :class="{ selected: selectedCandidate?.key === item.key }"
            @click="selectedCandidate = item"
          >
            <span class="cand-name" :title="item.display_name">{{ item.display_name }}</span>
            <span>{{ item.width ? `${item.width}×${item.height}` : '—' }}</span>
            <span>{{ item.count ?? '—' }}</span>
            <span>{{ formatBytes(item.size_bytes) }}</span>
            <span class="cand-pick">{{ selectedCandidate?.key === item.key ? '已选' : '选择' }}</span>
          </div>
          <p v-if="!candidates.length" class="empty-block">暂无可用影像（先在项目工作台登记影像或跑一次解译）</p>
        </div>

        <div v-if="selectedCandidate" class="tool-grid">
          <article class="tool-card">
            <h3>范围裁剪</h3>
            <div class="map-shell">
              <div ref="clipMapRef" class="clip-map"></div>
              <p v-if="previewLoading" class="map-hint">影像预览生成中…</p>
              <p v-else-if="previewError" class="error-text map-hint">{{ previewError }}</p>
            </div>
            <div class="tool-row">
              <label>范围来源</label>
              <select v-model="clipSource">
                <option value="draw">影像上手绘多边形</option>
                <option value="mine">矿山边界</option>
              </select>
              <select v-if="clipSource === 'mine'" v-model="clipMineFid">
                <option :value="null" disabled>选择矿山</option>
                <option v-for="fid in mineFids" :key="fid" :value="fid">矿山 {{ fid }}</option>
              </select>
            </div>
            <div v-if="clipSource === 'draw'" class="draw-ops">
              <button class="secondary-btn" type="button" @click="clearDraw">清除重画</button>
              <span class="muted-text">{{ drawLatLngs.length }} 个顶点{{ polygonClosed ? '（已闭合）' : '，双击闭合' }}</span>
            </div>
            <p class="draw-hint muted-text">在影像上点击添加顶点，双击闭合多边形；范围来源为矿山边界时叠加所选矿山辅助定位</p>
            <div class="tool-row">
              <label>边界外扩（米）</label>
              <input v-model.number="clipBuffer" type="number" min="0" max="5000" step="50" />
            </div>
            <button class="primary-btn" type="button" :disabled="!clipReady || busy" @click="runClip">
              {{ busy === 'clip' ? '裁剪中…' : '执行裁剪' }}
            </button>
            <p v-if="clipResult" class="result-line ok">✓ {{ clipResult }}</p>
          </article>

          <article class="tool-card">
            <h3>自动切片</h3>
            <div class="tool-row">
              <label>切片方式</label>
              <select v-model="sliceMode">
                <option value="grid_pixels">固定像素</option>
                <option value="grid_area">固定面积（m²）</option>
              </select>
            </div>
            <div class="tool-row">
              <label>{{ sliceMode === 'grid_pixels' ? '每片像素（边长）' : '每片面积（m²）' }}</label>
              <input
                v-model.number="sliceSizeValue"
                type="number"
                :min="sliceMode === 'grid_pixels' ? 64 : 1"
              />
            </div>
            <div class="tool-row">
              <label>片数上限</label>
              <input v-model.number="sliceLimit" type="number" min="1" max="256" />
              <button class="secondary-btn" type="button" @click="savePreset">存为常用参数</button>
            </div>
            <p v-if="savedPresetHint" class="result-line ok">{{ savedPresetHint }}</p>
            <button class="primary-btn" type="button" :disabled="!sliceReady || busy" @click="runSlice">
              {{ busy === 'slice' ? '切片中…' : '执行切片' }}
            </button>
            <p v-if="sliceResult" class="result-line" :class="sliceSummary ? 'ok' : 'error'">{{ sliceResult }}</p>
            <div v-if="sliceItems.length" class="slice-list">
              <div v-for="item in sliceItems.slice(0, 8)" :key="item.dataset_id" class="slice-row">
                <span>r{{ item.row }}c{{ item.col }} · {{ item.width }}×{{ item.height }}</span>
                <a :href="interpretHref" target="_blank" class="slice-link">去解译</a>
              </div>
              <p v-if="sliceItems.length > 8" class="muted-text">…共 {{ sliceItems.length }} 片（已登记项目数据集）</p>
            </div>
          </article>
        </div>
      </template>
    </section>
  </div>
</template>

<script setup>
import axios from 'axios';
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { getSelectedProjectId, setSelectedProjectId } from '../services/projectSelectionStore.js';
import { formatLifecycleStatus } from '../utils/projectStatusLabels.js';

const emit = defineEmits(['go-map']);

const MINER_API_BASE_URL = import.meta.env.VITE_MINER_API_BASE_URL || '';
const GEOVIEW_BASE_URL = `${window.location.protocol}//${window.location.hostname}:3000`;
const PRESET_KEY = 'imagery_tool_presets_v1';

const projects = ref([]);
const selectedProjectId = ref(null);
const candidates = ref([]);
const candidatesLoading = ref(false);
const error = ref('');
const selectedCandidate = ref(null);
const busy = ref(null);

// 裁剪状态
const clipSource = ref('draw');
const clipMineFid = ref(null);
const clipBuffer = ref(0);
const mineFids = ref([]);
const clipResult = ref('');

// 切片状态
const sliceMode = ref('grid_pixels');
const slicePixels = ref(1024);
const sliceArea = ref(1_000_000);
const sliceLimit = ref(64);
const sliceResult = ref('');
const sliceItems = ref([]);
const sliceSummary = ref(false);
const savedPresetHint = ref('');

const formatBytes = (bytes) => {
  if (bytes === null || bytes === undefined) return '—';
  const size = Number(bytes);
  if (!Number.isFinite(size) || size < 0) return '—';
  if (size >= 1024 ** 3) return `${(size / 1024 ** 3).toFixed(2)}GB`;
  if (size >= 1024 ** 2) return `${(size / 1024 ** 2).toFixed(1)}MB`;
  if (size >= 1024) return `${(size / 1024).toFixed(1)}KB`;
  return `${Math.round(size)}B`;
};

const loadProjects = async () => {
  try {
    const res = await axios.get(`${MINER_API_BASE_URL}/api/projects`);
    projects.value = res.data?.data?.items || [];
    // 共享项目上下文：模块页首次载入时自动回选工作台/其他模块选中的项目（gui-audit #5）
    const sharedProjectId = getSelectedProjectId();
    if (!selectedProjectId.value && sharedProjectId && projects.value.some((p) => p.id === sharedProjectId)) {
      selectedProjectId.value = sharedProjectId;
      loadCandidates();
    }
  } catch (_) {
    error.value = '项目列表读取失败';
  }
};

let candidatesSeq = 0;

const loadCandidates = async () => {
  setSelectedProjectId(Number(selectedProjectId.value) || null); // 用户手选同步共享上下文
  // 刷新后按稳定标识回填选中（收官审查 P1：切片成功后清空选中曾使工具卡与"去解译"自毁）
  const previous = selectedCandidate.value;
  candidates.value = [];
  selectedCandidate.value = null;
  mineFids.value = [];
  clipMineFid.value = null;
  if (!selectedProjectId.value) return;
  // 竞态门控：快速切换项目时旧项目的候选清单不得覆盖新项目的——否则 A 项目的
  // dataset_id 留在 B 项目页上，裁剪/切片引用跨项目资产（2026-10-24 审查 P2-6）。
  // 独立计数器——不得复用其他请求的计数器（并发触发时共用会让先发响应被后发作废）
  const seq = (candidatesSeq = (candidatesSeq || 0) + 1);
  const projectIdAtRequest = selectedProjectId.value;
  candidatesLoading.value = true;
  error.value = '';
  try {
    const [candRes, geoRes] = await Promise.all([
      axios.get(`${MINER_API_BASE_URL}/api/projects/${projectIdAtRequest}/imagery/candidates`),
      axios.get(`${MINER_API_BASE_URL}/api/projects/${projectIdAtRequest}/geojson`).catch(() => null),
    ]);
    if (seq !== candidatesSeq || projectIdAtRequest !== selectedProjectId.value) return;
    const items = candRes.data?.data?.items || [];
    candidates.value = items.map((item, index) => ({
      ...item,
      key: `${item.dataset_id ?? 'f'}-${item.display_name}-${index}`,
    }));
    if (previous) {
      const restored = candidates.value.find(
        (item) => (previous.dataset_id != null && item.dataset_id === previous.dataset_id)
          || (item.display_name === previous.display_name && item.width === previous.width && item.height === previous.height),
      );
      if (restored) selectedCandidate.value = restored;
    }
    const features = geoRes?.data?.data?.features || [];
    const fids = new Set();
    mineFeatures.clear();
    for (const feature of features) {
      const fid = Number(feature?.properties?.FID_1);
      if (Number.isSafeInteger(fid) && fid > 0) {
        fids.add(fid);
        if (feature.geometry) mineFeatures.set(fid, feature.geometry);
      }
    }
    mineFids.value = Array.from(fids).sort((a, b) => a - b).slice(0, 500);
    if (mineFids.value.length && clipMineFid.value === null) clipMineFid.value = mineFids.value[0];
  } catch (e) {
    if (seq !== candidatesSeq || projectIdAtRequest !== selectedProjectId.value) return;
    error.value = e?.response?.data?.msg || '影像清单读取失败';
  } finally {
    if (seq === candidatesSeq) candidatesLoading.value = false;
  }
};

// ===== 影像上手绘（Leaflet 地理绑定，2026-10-24 审查 P1-8）=====
// 旧实现：SVG 归一化坐标 × 硬编码假想 bbox 换算经纬度——画在哪张影像上全凭
// 假想范围，与所选影像零地理关联。现为所选影像拉降采样预览 PNG 叠加成底图，
// 多边形顶点直接是点击处的真实经纬度，所见即所得。

let clipMap = null;          // L.Map：裁剪卡片内嵌迷你地图
let overlayLayer = null;     // L.ImageOverlay：当前选中影像预览
let drawLayerGroup = null;   // 手绘多边形（顶点/边线）
let mineOverlayLayer = null; // 矿山边界辅助层
const clipMapRef = ref(null);
const drawLatLngs = ref([]); // [lat, lng] 顶点序列
const polygonClosed = ref(false);
const previewError = ref('');
const previewLoading = ref(false);
const mineFeatures = new Map(); // fid -> GeoJSON geometry（矿山边界辅助）

const ensureClipMap = () => {
  if (clipMap || !clipMapRef.value) return;
  clipMap = L.map(clipMapRef.value, {
    center: [26.0, 100.2],
    zoom: 12,
    attributionControl: false,
    doubleClickZoom: false, // 双击保留给“闭合多边形”
  });
  clipMap.on('click', (event) => {
    if (clipSource.value !== 'draw' || polygonClosed.value) return;
    drawLatLngs.value = [...drawLatLngs.value, [event.latlng.lat, event.latlng.lng]];
    renderDrawLayer();
  });
  clipMap.on('dblclick', () => {
    if (clipSource.value === 'draw' && drawLatLngs.value.length >= 3) {
      polygonClosed.value = true;
      renderDrawLayer();
    }
  });
};

const destroyClipMap = () => {
  if (!clipMap) return;
  clipMap.remove();
  clipMap = null;
  overlayLayer = null;
  drawLayerGroup = null;
  mineOverlayLayer = null;
};

const renderDrawLayer = () => {
  if (drawLayerGroup) {
    drawLayerGroup.remove();
    drawLayerGroup = null;
  }
  if (!clipMap || !drawLatLngs.value.length) return;
  drawLayerGroup = L.layerGroup().addTo(clipMap);
  const points = drawLatLngs.value;
  // 图层纯展示（interactive:false）：点击/双击穿透到地图本体，顶点与线上也能继续落笔
  if (polygonClosed.value) {
    L.polygon(points, { color: '#2f6f61', weight: 2, fillColor: '#4ecdcd', fillOpacity: 0.25, interactive: false }).addTo(drawLayerGroup);
  } else {
    L.polyline(points, { color: '#2f6f61', weight: 2, dashArray: '6 6', interactive: false }).addTo(drawLayerGroup);
  }
  for (const point of points) {
    L.circleMarker(point, { radius: 4, color: '#2f6f61', fillColor: '#2f6f61', fillOpacity: 1, interactive: false }).addTo(drawLayerGroup);
  }
};

const clearDraw = () => {
  drawLatLngs.value = [];
  polygonClosed.value = false;
  renderDrawLayer();
};

const drawGeometry = computed(() => {
  if (!polygonClosed.value || drawLatLngs.value.length < 3) return null;
  // [lat, lng] → GeoJSON [lng, lat]，保留 6 位小数与旧实现对齐
  const ring = drawLatLngs.value.map(([lat, lng]) => [Number(lng.toFixed(6)), Number(lat.toFixed(6))]);
  ring.push(ring[0]);
  return { type: 'Polygon', coordinates: [ring] };
});

let previewSeq = 0; // 预览竞态计数器——不得复用其他请求的计数器（惯例同 loadCandidates）

const loadPreview = async () => {
  const candidate = selectedCandidate.value;
  if (overlayLayer) {
    overlayLayer.remove();
    overlayLayer = null;
  }
  previewError.value = '';
  if (!candidate) return;
  // 契约（AGENTS §6）：浏览器不提交服务器物理路径；dataset_id 优先，输入目录候选用相对 storage_key
  const source = candidate.dataset_id ? String(candidate.dataset_id) : candidate.storage_key;
  const seq = (previewSeq = (previewSeq || 0) + 1);
  previewLoading.value = true;
  try {
    const res = await axios.get(
      `${MINER_API_BASE_URL}/api/projects/${selectedProjectId.value}/imagery/preview`,
      { params: { source } },
    );
    if (seq !== previewSeq) return;
    const data = res.data?.data || {};
    const { west, south, east, north } = data.bounds || {};
    if (![west, south, east, north].every(Number.isFinite)) throw new Error('预览缺少有效范围');
    ensureClipMap();
    if (!clipMap) return;
    overlayLayer = L.imageOverlay(
      `data:image/png;base64,${data.png_base64}`,
      [[south, west], [north, east]],
    ).addTo(clipMap);
    clipMap.fitBounds([[south, west], [north, east]]);
    clipMap.invalidateSize();
  } catch (e) {
    if (seq !== previewSeq) return;
    previewError.value = e?.response?.data?.msg || '影像预览生成失败，无法在此影像上手绘（可改用矿山边界裁剪）';
  } finally {
    if (seq === previewSeq) previewLoading.value = false;
  }
};

const updateMineOverlay = () => {
  if (mineOverlayLayer) {
    mineOverlayLayer.remove();
    mineOverlayLayer = null;
  }
  if (!clipMap || clipSource.value !== 'mine' || clipMineFid.value == null) return;
  const geometry = mineFeatures.get(Number(clipMineFid.value));
  if (!geometry) return;
  mineOverlayLayer = L.geoJSON(geometry, { style: { color: '#e67e22', weight: 2, fillOpacity: 0.08 } }).addTo(clipMap);
};

watch(selectedCandidate, async () => {
  clearDraw(); // 换影像后旧多边形与新底图无地理关联
  await nextTick();
  if (!selectedCandidate.value) {
    destroyClipMap();
    return;
  }
  ensureClipMap();
  await loadPreview();
  updateMineOverlay();
});

watch([clipSource, clipMineFid], () => updateMineOverlay());

onBeforeUnmount(() => destroyClipMap());

const clipReady = computed(() => {
  if (!selectedCandidate.value) return false;
  if (clipSource.value === 'mine') return Boolean(clipMineFid.value);
  return polygonClosed.value && Boolean(drawGeometry.value);
});

const runClip = async () => {
  busy.value = 'clip';
  clipResult.value = '';
  try {
    const body = {
      // 契约（AGENTS §6）：浏览器不提交服务器物理路径；输入目录候选用相对 storage_key
      source: selectedCandidate.value.dataset_id
        ? String(selectedCandidate.value.dataset_id)
        : selectedCandidate.value.storage_key,
      buffer_meters: clipBuffer.value || 0,
      ...(clipSource.value === 'mine'
        ? { mine_fid: clipMineFid.value }
        : { geometry: drawGeometry.value }),
    };
    const res = await axios.post(
      `${MINER_API_BASE_URL}/api/projects/${selectedProjectId.value}/imagery/clip`,
      body,
    );
    const data = res.data?.data || {};
    clipResult.value = `裁剪完成：${data.width}×${data.height} 像素，已登记数据集 #${data.dataset_id}`;
    await loadCandidates();
  } catch (e) {
    clipResult.value = `✗ ${e?.response?.data?.msg || '裁剪失败'}`;
    window.alert(e?.response?.data?.msg || '裁剪失败');
  } finally {
    busy.value = null;
  }
};

// ===== 切片 =====
const sliceSizeValue = computed({
  get: () => (sliceMode.value === 'grid_pixels' ? slicePixels.value : sliceArea.value),
  set: (value) => {
    if (sliceMode.value === 'grid_pixels') slicePixels.value = Number(value);
    else sliceArea.value = Number(value);
  },
});

const sliceReady = computed(() => {
  if (!selectedCandidate.value) return false;
  return sliceMode.value === 'grid_pixels'
    ? Number(slicePixels.value) >= 64
    : Number(sliceArea.value) > 0;
});

const runSlice = async () => {
  busy.value = 'slice';
  sliceResult.value = '';
  sliceItems.value = [];
  sliceSummary.value = false;
  try {
    const body = {
      // 契约（AGENTS §6）：浏览器不提交服务器物理路径；输入目录候选用相对 storage_key
      source: selectedCandidate.value.dataset_id
        ? String(selectedCandidate.value.dataset_id)
        : selectedCandidate.value.storage_key,
      mode: sliceMode.value,
      limit: sliceLimit.value || 64,
      ...(sliceMode.value === 'grid_pixels'
        ? { tile_pixels: Number(slicePixels.value) }
        : { tile_area_m2: Number(sliceArea.value) }),
    };
    const res = await axios.post(
      `${MINER_API_BASE_URL}/api/projects/${selectedProjectId.value}/imagery/slice`,
      body,
    );
    const data = res.data?.data || {};
    sliceSummary.value = true;
    sliceResult.value = `切片完成：${data.created} 片（网格 ${data.grid.cols}×${data.grid.rows}）${data.skipped?.length ? `，丢弃边片 ${data.skipped.length}` : ''}`;
    sliceItems.value = data.items || [];
    await loadCandidates();
  } catch (e) {
    sliceResult.value = `✗ ${e?.response?.data?.msg || '切片失败'}`;
    window.alert(e?.response?.data?.msg || '切片失败');
  } finally {
    busy.value = null;
  }
};

const savePreset = () => {
  try {
    const preset = {
      clipSource: clipSource.value,
      clipBuffer: clipBuffer.value,
      sliceMode: sliceMode.value,
      slicePixels: slicePixels.value,
      sliceArea: sliceArea.value,
      sliceLimit: sliceLimit.value,
    };
    localStorage.setItem(PRESET_KEY, JSON.stringify(preset));
    savedPresetHint.value = '已保存常用参数';
    setTimeout(() => { savedPresetHint.value = ''; }, 2000);
  } catch (_) {
    savedPresetHint.value = '保存失败（浏览器存储不可用）';
  }
};

const restorePreset = () => {
  try {
    const raw = localStorage.getItem(PRESET_KEY);
    if (!raw) return;
    const preset = JSON.parse(raw);
    if (typeof preset.clipBuffer === 'number') clipBuffer.value = preset.clipBuffer;
    if (['draw', 'mine'].includes(preset.clipSource)) clipSource.value = preset.clipSource;
    if (['grid_pixels', 'grid_area'].includes(preset.sliceMode)) sliceMode.value = preset.sliceMode;
    if (Number.isFinite(preset.slicePixels)) slicePixels.value = preset.slicePixels;
    if (Number.isFinite(preset.sliceArea)) sliceArea.value = preset.sliceArea;
    if (Number.isFinite(preset.sliceLimit)) sliceLimit.value = preset.sliceLimit;
  } catch (_) { /* 忽略坏预设 */ }
};

const interpretHref = computed(() => {
  const target = new URL(`${GEOVIEW_BASE_URL}/segmentation`);
  target.searchParams.set('project_id', String(selectedProjectId.value || ''));
  return target.toString();
});

const goWorkspace = () => {
  if (selectedProjectId.value) emit('go-map', selectedProjectId.value);
};

restorePreset();
loadProjects();
</script>

<style scoped>
.imagery-view { max-width: 1020px; margin: 0 auto; padding: 28px 24px; color: #264b45; }
.view-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 18px; }
.view-header h1 { margin: 4px 0 6px; font-size: 22px; }
.kicker { color: #5a7d75; font-size: 13px; margin: 0; }
.muted-text { color: #5a7d75; font-size: 12px; }

.view-body {
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid rgba(38, 75, 69, 0.15);
  border-radius: 10px;
  padding: 18px 20px;
}

.row { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; flex-wrap: wrap; }
.row label { font-size: 14px; }
.row select, .tool-row select, .tool-row input {
  padding: 7px 10px; border-radius: 6px;
  border: 1px solid rgba(38, 75, 69, 0.3); font-size: 13px;
}

h3 { font-size: 14px; color: #2f6f61; margin: 12px 0 8px; }

.candidate-table { border: 1px solid rgba(38, 75, 69, 0.12); border-radius: 8px; overflow: hidden; margin-bottom: 14px; max-height: 240px; overflow-y: auto; }
.candidate-row {
  display: grid; grid-template-columns: 2fr 1fr 0.6fr 1fr 0.6fr; gap: 8px;
  padding: 8px 12px; font-size: 13px; align-items: center;
  border-bottom: 1px solid rgba(38, 75, 69, 0.06); cursor: pointer;
}
.candidate-row.head { background: rgba(38, 75, 69, 0.06); font-weight: 600; cursor: default; }
.candidate-row:not(.head):hover { background: rgba(38, 75, 69, 0.04); }
.candidate-row.selected { background: rgba(47, 111, 97, 0.12); }
.cand-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cand-pick { color: #2f6f61; font-size: 12px; text-align: right; }

.tool-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(380px, 1fr)); gap: 14px; }
.tool-card {
  background: rgba(38, 75, 69, 0.03);
  border: 1px solid rgba(38, 75, 69, 0.12);
  border-radius: 8px; padding: 14px;
}
.tool-card h3 { margin: 0 0 10px; }
.tool-row { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }
.tool-row label { font-size: 13px; min-width: 110px; }
.tool-row input { width: 130px; }

.draw-hint { font-size: 12px; color: #5a7d75; margin-bottom: 6px; }
.draw-ops { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }

.map-shell { position: relative; margin-bottom: 10px; }
.clip-map {
  width: 100%; height: 320px; border-radius: 8px;
  border: 1px solid rgba(38, 75, 69, 0.2); background: #e8efec; cursor: crosshair;
}
.map-hint {
  position: absolute; top: 8px; left: 8px; margin: 0;
  background: rgba(255, 255, 255, 0.88); border-radius: 6px; padding: 4px 10px; font-size: 12px;
}

.primary-btn { background: #2f6f61; color: #fff; border: none; border-radius: 6px; padding: 8px 18px; cursor: pointer; font-size: 13px; }
.primary-btn:hover { background: #264b45; }
.primary-btn:disabled { opacity: 0.45; cursor: not-allowed; }
.secondary-btn { background: #fff; border: 1px solid #2f6f61; color: #2f6f61; border-radius: 6px; padding: 6px 12px; cursor: pointer; font-size: 12px; }
.secondary-btn:hover { background: rgba(47, 111, 97, 0.08); }

.result-line { font-size: 13px; margin-top: 8px; }
.result-line.ok { color: #00a383; }
.result-line.error { color: #c0392b; }

.slice-list { margin-top: 10px; display: flex; flex-direction: column; gap: 4px; }
.slice-row {
  display: flex; justify-content: space-between; align-items: center;
  background: rgba(255, 255, 255, 0.6); border-radius: 6px; padding: 5px 10px; font-size: 12px;
}
.slice-link { color: #2f6f61; text-decoration: none; font-weight: 600; }

.error-text { color: #c0392b; font-size: 13px; }
.empty-block { color: #7ba39a; font-size: 14px; text-align: center; padding: 18px 0; }
</style>
