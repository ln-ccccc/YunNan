<template>
  <div id="map" class="h-screen w-full"></div>
  <div
    v-if="showPanel"
    class="fixed bottom-5 right-5 w-[480px] bg-white shadow-lg rounded-lg p-3"
  >
    <h3 class="text-lg font-bold mb-2">矿山 {{ currentFID }}</h3>
    <div class="border-b mb-2 flex space-x-4 text-sm">
      <button
        class="pb-1 border-b-2"
        :class="tab==='matrix' ? 'border-blue-500 text-blue-600' : 'border-transparent text-gray-500'"
        @click="tab='matrix'"
      >
        变化矩阵
      </button>
      <button
        class="pb-1 border-b-2"
        :class="tab==='details' ? 'border-blue-500 text-blue-600' : 'border-transparent text-gray-500'"
        @click="tab='details'"
      >
        详细变化信息
      </button>
      <button
        class="pb-1 border-b-2 ml-auto text-gray-400 hover:text-gray-600 border-transparent"
        @click="showPanel=false"
      >
        关闭
      </button>
    </div>

    <div v-if="tab==='matrix'" class="text-sm">
      <p>NDVI 均值：{{ ndviMean }}</p>
      <p>
        趋势：
        <span :style="{ color: ndviTrend > 0 ? 'green' : (ndviTrend < 0 ? 'red' : 'gray') }">
          {{ ndviTrend }}
        </span>
      </p>
      <!-- 已有矩阵视图在其他前端中，本 miner 只保留简要信息 -->
    </div>

    <div v-else class="text-sm">
      <div v-if="indexSeries.years.length === 0">
        暂无多年份指标数据
      </div>
      <div v-else>
        <div class="flex items-center mb-2">
          <span class="mr-2">指标：</span>
          <select
            v-model="selectedMetric"
            class="border rounded px-2 py-1 text-sm"
          >
            <option value="ndvi">NDVI</option>
            <option value="ndbi">NDBI</option>
            <option value="ndwi">NDWI</option>
            <option value="ndsi">NDSI</option>
          </select>
        </div>
        <div class="h-40">
          <svg
            v-if="chartPoints.length > 1"
            viewBox="0 0 320 160"
            class="w-full h-full border rounded bg-gray-50"
          >
            <polyline
              :points="chartPoints.map(p => p.x + ',' + p.y).join(' ')"
              fill="none"
              stroke="#2563eb"
              stroke-width="2"
            />
            <circle
              v-for="(p, idx) in chartPoints"
              :key="idx"
              :cx="p.x"
              :cy="p.y"
              r="3"
              fill="#2563eb"
            />
          </svg>
          <div v-else class="text-gray-400 flex items-center justify-center h-full">
            指标点数不足以绘制折线
          </div>
        </div>
        <div class="mt-2 text-xs text-gray-500">
          年份: {{ indexSeries.years.join(', ') }}
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from "vue";
import * as L from "leaflet";
import axios from "axios";

const rawBase = import.meta.env.VITE_MINER_API_BASE_URL;
const API_BASE_URL = rawBase ? String(rawBase).replace(/\/$/, "") : "";
const apiUrl = (p) => `${API_BASE_URL}${p}`;

const showPanel = ref(false);
const currentFID = ref("");
const ndviMean = ref(0);
const ndviTrend = ref(0);
const tab = ref("matrix");
const selectedMetric = ref("ndvi");
const indexSeries = ref({
  years: [],
  ndvi: [],
  ndbi: [],
  ndwi: [],
  ndsi: [],
});

const chartPoints = computed(() => {
  const ys = indexSeries.value.years;
  const vals = indexSeries.value[selectedMetric.value] || [];
  if (!ys || ys.length === 0 || vals.length === 0) return [];
  const n = Math.min(ys.length, vals.length);
  const xs = ys.slice(0, n);
  const vs = vals.slice(0, n);
  const minYear = Math.min(...xs);
  const maxYear = Math.max(...xs);
  const minVal = Math.min(...vs);
  const maxVal = Math.max(...vs);
  const padX = 20;
  const padY = 20;
  const width = 320 - padX * 2;
  const height = 160 - padY * 2;
  return xs.map((year, i) => {
    const v = vs[i];
    const tx = maxYear === minYear ? 0.5 : (year - minYear) / (maxYear - minYear);
    const ty = maxVal === minVal ? 0.5 : (v - minVal) / (maxVal - minVal);
    const x = padX + tx * width;
    const y = padY + (1 - ty) * height;
    return { x, y };
  });
});

onMounted(async () => {
    const map = L.map("map").setView([25.6, 100.2], 8);
  const provider = String(import.meta.env.VITE_MINER_MAP_PROVIDER || 'offline').toLowerCase();
  const tdtKey = String(import.meta.env.VITE_TDT_KEY || '').trim();
  const makeTdt = (kind) => {
    const kindMap = { vec: 'vec_w', img: 'img_w', ter: 'ter_w' };
    const code = kindMap[kind];
    if (!code || !tdtKey) return null;
    return L.tileLayer(
      `https://t{s}.tianditu.gov.cn/DataServer?T=${code}&x={x}&y={y}&l={z}&tk=${tdtKey}`,
      { subdomains: ['0', '1', '2', '3', '4', '5', '6', '7'], maxZoom: 18 }
    );
  };
  const makeGaode = (kind) => {
    const kindMap = {
      vec: 'https://webrd0{s}.is.autonavi.com/appmaptile?style=7&x={x}&y={y}&z={z}',
      img: 'https://webst0{s}.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}',
      ter: 'https://webrd0{s}.is.autonavi.com/appmaptile?style=7&x={x}&y={y}&z={z}',
    };
    const url = kindMap[kind];
    if (!url) return null;
    return L.tileLayer(url, { subdomains: ['1', '2', '3', '4'], maxZoom: 19 });
  };
  const makeOffline = (label) => L.gridLayer({
    tileSize: 256,
    minZoom: 0,
    maxZoom: 19,
    createTile: (coords, done) => {
      const tile = document.createElement('canvas');
      tile.width = 256;
      tile.height = 256;
      const ctx = tile.getContext('2d');
      if (ctx) {
        ctx.fillStyle = '#f5f7fb';
        ctx.fillRect(0, 0, 256, 256);
        ctx.strokeStyle = 'rgba(31,45,61,0.10)';
        ctx.strokeRect(0.5, 0.5, 255, 255);
        ctx.fillStyle = 'rgba(31,45,61,0.55)';
        ctx.font = '12px sans-serif';
        ctx.fillText(label, 10, 18);
        ctx.fillStyle = 'rgba(31,45,61,0.35)';
        ctx.font = '11px monospace';
        ctx.fillText(`z:${coords.z} x:${coords.x} y:${coords.y}`, 10, 36);
      }
      done(null, tile);
      return tile;
    }
  });
  const tdt = makeTdt('img') || makeTdt('vec') || makeTdt('ter');
  const baseLayer = (provider === 'tianditu' && tdt)
    ? tdt
    : (provider === 'gaode')
      ? (makeGaode('img') || makeGaode('vec') || makeOffline('离线底图'))
      : (provider === 'osm')
        ? L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png")
        : makeOffline('离线底图');
  baseLayer.addTo(map);

  const res = await axios.get(apiUrl("/api/mines"));
  const mines = res.data;

    mines.forEach((mine) => {
    const geom = mine.geometry;
    const polygon = L.geoJSON(geom, { color: "blue" }).addTo(map);
    polygon.on("click", async () => {
        currentFID.value = mine.fid;
        tab.value = "matrix";
        showPanel.value = true;

        // 简要 NDVI 信息
        try {
          const ndviRes = await axios.get(apiUrl(`/api/mines/ndvi?fid=${mine.fid}`));
          const data = ndviRes.data;
          if (!data || data.error) {
            ndviMean.value = 0;
            ndviTrend.value = 0;
          } else {
            ndviMean.value = data.ndvi_mean;
            ndviTrend.value = data.ndvi_trend;
          }
        } catch {
          ndviMean.value = 0;
          ndviTrend.value = 0;
        }

        // 指标时间序列
        try {
          const idxRes = await axios.get(apiUrl(`/api/mines/indices?fid=${mine.fid}`));
          const d = idxRes.data;
          const years = (d.ndvi?.data || []).map((p) => p.year);
          indexSeries.value = {
            years,
            ndvi: (d.ndvi?.data || []).map((p) => p.value),
            ndbi: (d.ndbi?.data || []).map((p) => p.value),
            ndwi: (d.ndwi?.data || []).map((p) => p.value),
            ndsi: (d.ndsi?.data || []).map((p) => p.value),
          };
        } catch {
          indexSeries.value = { years: [], ndvi: [], ndbi: [], ndwi: [], ndsi: [] };
        }
    });
  });
});
</script>
