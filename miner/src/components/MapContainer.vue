<template>
  <div class="map-container" ref="mapContainer">
    <div id="map" ref="mapElement"></div>

    <div class="map-legend glass-panel" :class="{ 'shifted-left': leftCollapsed }">
      <div class="legend-item"><span class="dot treated"></span> 已治理</div>
      <div class="legend-item"><span class="dot untreated"></span> 未治理</div>
      <div class="legend-item"><span class="dot unknown"></span> 未知/其他</div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref, watch, defineProps, defineEmits, defineExpose } from 'vue';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { createTileFallbackState, noteTileError, noteTileLoad } from '../map/tileFallbackPolicy.js';

const props = defineProps({
  minesData: Array,
  leftCollapsed: Boolean,
  rightCollapsed: Boolean,
  tileUrl: { type: String, default: '' },
  minZoom: { type: Number, default: 8 },
  maxZoom: { type: Number, default: 15 }
});

const emit = defineEmits(['select-mine']);

const map = ref(null);
const mineLayer = ref(null);
const currentLayer = ref('satellite');
const mapContainer = ref(null);
const mapElement = ref(null);
let baseMaps = {};
let resizeObserver = null;
let usingOfflineFallback = false;
let hasFitInitialBounds = false;
const tileFallbackState = createTileFallbackState();
const mapProvider = (import.meta.env.VITE_MINER_MAP_PROVIDER || 'offline').toLowerCase();
const tdtKey = String(import.meta.env.VITE_TDT_KEY || '').trim();
const localTileUrl = String(import.meta.env.VITE_MINER_LOCAL_TILE_URL || '').trim();
const localTileUrlBase = String(import.meta.env.VITE_MINER_LOCAL_TILE_URL_BASE || '').trim();
const localTileUrlSat = String(import.meta.env.VITE_MINER_LOCAL_TILE_URL_SAT || '').trim();
const localTileUrlTer = String(import.meta.env.VITE_MINER_LOCAL_TILE_URL_TER || '').trim();
const localTileTms = String(import.meta.env.VITE_MINER_LOCAL_TMS || '').trim() === '1';
const rawLocalTileMaxNativeZoom = Number(import.meta.env.VITE_MINER_LOCAL_MAX_NATIVE_ZOOM || 13);
const localTileMaxNativeZoom = Number.isFinite(rawLocalTileMaxNativeZoom) ? rawLocalTileMaxNativeZoom : 13;
const localTileDisplayMaxZoom = Math.max(localTileMaxNativeZoom, 15);

const makeTdtLayer = (kind) => {
  const kindMap = {
    vec: 'vec_w',
    cva: 'cva_w',
    img: 'img_w',
    cia: 'cia_w',
    ter: 'ter_w',
    cta: 'cta_w'
  };
  const layerCode = kindMap[kind];
  if (!layerCode || !tdtKey) return null;
  return L.tileLayer(
    `https://t{s}.tianditu.gov.cn/DataServer?T=${layerCode}&x={x}&y={y}&l={z}&tk=${tdtKey}`,
    { subdomains: ['0', '1', '2', '3', '4', '5', '6', '7'], maxZoom: 18 }
  );
};

const makeGaodeLayer = (kind) => {
  const kindMap = {
    base: 'https://webrd0{s}.is.autonavi.com/appmaptile?style=7&x={x}&y={y}&z={z}',
    satellite: 'https://webst0{s}.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}',
    terrain: 'https://webrd0{s}.is.autonavi.com/appmaptile?style=7&x={x}&y={y}&z={z}'
  };
  const url = kindMap[kind];
  if (!url) return null;
  return L.tileLayer(url, { subdomains: ['1', '2', '3', '4'], maxZoom: 19 });
};

const makeLocalLayer = (url, minZoom = 0, maxNativeZoom = localTileMaxNativeZoom) => {
  const value = String(url || '').trim();
  if (!value) return null;
  return L.tileLayer(value, {
    minZoom,
    maxZoom: Math.max(maxNativeZoom, 15),
    maxNativeZoom,
    crossOrigin: true,
    tms: localTileTms
  });
};

const makeOfflineLayer = (label) => L.gridLayer({
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
      ctx.lineWidth = 1;
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

const bindTileErrorFallback = (layer, onTileError) => {
  if (!layer || typeof layer.on !== 'function') return;
  if (layer instanceof L.TileLayer) {
    layer.on('tileload', () => noteTileLoad(tileFallbackState));
    layer.on('tileerror', onTileError);
  }
  if (typeof layer.eachLayer === 'function') {
    layer.eachLayer((child) => bindTileErrorFallback(child, onTileError));
  }
};

const initMap = () => {
  if (!mapElement.value) return;
  map.value = L.map(mapElement.value, { zoomControl: false, attributionControl: false }).setView([25.6, 100.2], 9);
  L.control.zoom({ position: 'bottomright' }).addTo(map.value);

  const tdtBase = makeTdtLayer('vec');
  const tdtLabel = makeTdtLayer('cva');
  const tdtSat = makeTdtLayer('img');
  const tdtSatLabel = makeTdtLayer('cia');
  const tdtTer = makeTdtLayer('ter');
  const tdtTerLabel = makeTdtLayer('cta');

  const provider = props.tileUrl ? 'project' : (mapProvider || 'offline');
  const useTdt = provider === 'tianditu' && tdtBase && tdtSat && tdtTer;
  const useGaode = provider === 'gaode';
  if (provider === 'local' || provider === 'project') {
    map.value.setMaxZoom(provider === 'project' ? Math.max(props.maxZoom, 15) : localTileDisplayMaxZoom);
  }

  const offlineMaps = {
    base: makeOfflineLayer('离线底图-标准'),
    satellite: makeOfflineLayer('离线底图-影像'),
    terrain: makeOfflineLayer('离线底图-地形')
  };

  if (useTdt) {
    baseMaps = {
      base: L.layerGroup([tdtBase, tdtLabel].filter(Boolean)),
      satellite: L.layerGroup([tdtSat, tdtSatLabel].filter(Boolean)),
      terrain: L.layerGroup([tdtTer, tdtTerLabel].filter(Boolean))
    };
  } else if (useGaode) {
    const gdBase = makeGaodeLayer('base');
    const gdSat = makeGaodeLayer('satellite');
    const gdTer = makeGaodeLayer('terrain');
    baseMaps = {
      base: gdBase,
      satellite: gdSat,
      terrain: gdTer
    };
  } else if (provider === 'osm') {
    baseMaps = {
      base: L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19 }),
      satellite: L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { maxZoom: 19 }),
      terrain: L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', { maxZoom: 17 })
    };
  } else if (provider === 'project') {
    baseMaps = {
      base: makeLocalLayer(props.tileUrl, props.minZoom, props.maxZoom),
      satellite: makeLocalLayer(props.tileUrl, props.minZoom, props.maxZoom),
      terrain: makeLocalLayer(props.tileUrl, props.minZoom, props.maxZoom)
    };
  } else if (provider === 'local') {
    const fallback = localTileUrl || '/tiles/{z}/{x}/{y}.png';
    baseMaps = {
      base: makeLocalLayer(localTileUrlBase || fallback),
      satellite: makeLocalLayer(localTileUrlSat || fallback),
      terrain: makeLocalLayer(localTileUrlTer || fallback)
    };
  } else {
    baseMaps = offlineMaps;
  }

  const onTileError = () => {
    if (usingOfflineFallback || provider === 'offline' || !map.value) return;
    if (!noteTileError(tileFallbackState, { initialErrorThreshold: 4 })) return;
    usingOfflineFallback = true;
    Object.values(baseMaps).forEach((layer) => {
      if (layer && map.value.hasLayer(layer)) map.value.removeLayer(layer);
    });
    baseMaps = offlineMaps;
    const next = baseMaps[currentLayer.value] || baseMaps.base;
    if (next) next.addTo(map.value);
  };

  if (provider !== 'offline') {
    Object.values(baseMaps).forEach((layer) => bindTileErrorFallback(layer, onTileError));
  }

  const firstLayer = baseMaps[currentLayer.value] || baseMaps.base;
  if (firstLayer) firstLayer.addTo(map.value);
};

const getMineColor = (feature) => {
  const status = feature.properties.status_normalized || 'unknown';
  if (status === 'treated') return '#00b894';
  if (status === 'untreated') return '#ff7675';
  return '#fab1a0';
};

const fitMineLayerBounds = () => {
  if (!map.value || !mineLayer.value) return;
  const bounds = mineLayer.value.getBounds();
  if (!bounds || !bounds.isValid()) return;
  map.value.fitBounds(bounds, {
    paddingTopLeft: [24, 30],
    paddingBottomRight: [24, 30],
    maxZoom: 12
  });
};

const renderMapMarkers = () => {
  if (!map.value) return;
  if (mineLayer.value) map.value.removeLayer(mineLayer.value);

  const geoJsonData = { type: 'FeatureCollection', features: props.minesData };

  mineLayer.value = L.geoJSON(geoJsonData, {
    style: (feature) => {
      const color = getMineColor(feature);
      return {
        color,
        weight: 1.5,
        opacity: 0.95,
        fillColor: color,
        fillOpacity: 0.52
      };
    },
    onEachFeature: (feature, layer) => {
      const name = feature.properties.mine_name || feature.properties.name || `ID: ${feature.properties.FID_1}`;
      layer.bindTooltip(name, { direction: 'top', className: 'map-tooltip' });

      layer.on('click', () => {
        const bounds = layer.getBounds();
        const center = bounds.getCenter();
        emit('select-mine', { feature, center, bounds });
      });

      layer.on('mouseover', (event) => {
        event.target.setStyle({ weight: 3, fillOpacity: 0.7 });
      });
      layer.on('mouseout', (event) => {
        mineLayer.value.resetStyle(event.target);
      });
    }
  }).addTo(map.value);

  if (!hasFitInitialBounds && props.minesData.length > 0) {
    fitMineLayerBounds();
    hasFitInitialBounds = true;
  }
};

const flyToMine = (fid) => {
  if (!map.value || !mineLayer.value) return;
  let targetLayer = null;
  mineLayer.value.eachLayer((layer) => {
    if (layer.feature.properties.FID_1 === fid) {
      targetLayer = layer;
    }
  });

  if (targetLayer) {
    map.value.fitBounds(targetLayer.getBounds(), { maxZoom: 15 });
    targetLayer.openTooltip();
    const bounds = targetLayer.getBounds();
    const center = bounds.getCenter();
    emit('select-mine', { feature: targetLayer.feature, center, bounds });
  }
};

const invalidateSize = () => {
  if (map.value) map.value.invalidateSize();
};

watch(() => props.minesData, () => {
  renderMapMarkers();
}, { deep: true });

onMounted(() => {
  initMap();
  renderMapMarkers();

  if (mapContainer.value) {
    resizeObserver = new ResizeObserver(() => {
      invalidateSize();
    });
    resizeObserver.observe(mapContainer.value);
  }
});

onUnmounted(() => {
  resizeObserver?.disconnect();
});

defineExpose({
  flyToMine,
  invalidateSize
});
</script>

<style scoped>
.map-container {
  flex: 1;
  position: relative;
  background: radial-gradient(circle at center, #17334d 0%, #071827 78%);
  width: 100%;
  height: 100%;
  overflow: hidden;
}

#map {
  width: 100%;
  height: 100%;
  z-index: 1;
  background: transparent;
}

.map-legend {
  position: absolute;
  bottom: 20px;
  left: 286px;
  z-index: 10;
  padding: 10px 15px;
  display: flex;
  gap: 15px;
  transition: left 0.3s;
}

.map-legend.shifted-left {
  left: 60px;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
}

.dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
}

.dot.treated {
  background: #00b894;
}

.dot.untreated {
  background: #ff7675;
}

.dot.unknown {
  background: #fab1a0;
}

.glass-panel {
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 8px;
  padding: 12px;
}
</style>
