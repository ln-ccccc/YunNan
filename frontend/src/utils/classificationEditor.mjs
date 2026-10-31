const SECURE_TILE_TEMPLATE = /^\/api\/projects\/[1-9]\d*\/map-resources\/[1-9]\d*\/tiles\/\{z\}\/\{x\}\/\{y\}\.png$/;

function cloneGeometry(geometry) {
  if (!geometry || typeof geometry !== 'object') {
    throw new TypeError('编辑要素缺少几何对象');
  }
  if (!['Polygon', 'MultiPolygon'].includes(geometry.type)) {
    throw new TypeError('编辑器只支持 Polygon 或 MultiPolygon');
  }
  if (!Array.isArray(geometry.coordinates)) {
    throw new TypeError('编辑要素缺少坐标');
  }
  return JSON.parse(JSON.stringify({
    type: geometry.type,
    coordinates: geometry.coordinates,
  }));
}

function editableFeature(feature) {
  if (!feature || feature.type !== 'Feature' || !feature.properties || typeof feature.properties !== 'object') {
    throw new TypeError('编辑要素不是有效 Feature');
  }
  const classCode = feature.properties.class_code;
  if (!Number.isInteger(classCode) || classCode < 0 || classCode > 5) {
    throw new TypeError('编辑要素 class_code 必须为 0 到 5 的整数');
  }

  const properties = { class_code: classCode };
  const featureId = feature.properties.feature_id;
  if (featureId !== undefined && featureId !== null && featureId !== '') {
    if (typeof featureId !== 'string') {
      throw new TypeError('编辑要素 feature_id 必须为字符串');
    }
    properties.feature_id = featureId;
  }

  return {
    type: 'Feature',
    properties,
    geometry: cloneGeometry(feature.geometry),
  };
}

export function buildEditableFeatureCollection(featureCollection) {
  if (
    !featureCollection
    || featureCollection.type !== 'FeatureCollection'
    || !Array.isArray(featureCollection.features)
  ) {
    throw new TypeError('编辑器未获得有效 FeatureCollection');
  }
  return {
    type: 'FeatureCollection',
    features: featureCollection.features.map(editableFeature),
  };
}

export function resolveSecureTileTemplate(apiTileUrl, backendBaseUrl) {
  if (typeof apiTileUrl !== 'string' || !SECURE_TILE_TEMPLATE.test(apiTileUrl)) {
    return null;
  }
  try {
    const backendUrl = new URL(backendBaseUrl);
    return `${backendUrl.origin}${apiTileUrl}`;
  } catch (_) {
    return null;
  }
}

const ZOOM_FLOOR = 0;
const ZOOM_CEILING = 22;

function toZoomLevel(value) {
  // null/undefined/空串表示"未声明"，不得经 Number(null)=0 误判为 0 级
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < ZOOM_FLOOR || parsed > ZOOM_CEILING) return null;
  return parsed;
}

/**
 * 把 map_manifest 的 min_zoom/max_zoom 收敛为 MapLibre raster source 的
 * minzoom/maxzoom（2026-10-31 审计 P2：不应用时放大超过金字塔最大级别会继续
 * 请求不存在的瓦片，404 轰炸且底图空白）。仅接受 [0,22] 整数；min>max 时丢弃
 * min 保留 max（防越级 404 是主诉求）；字段缺失或非法时不加限制，兼容旧 manifest。
 */
export function resolveTileZoomRange(manifest) {
  const range = {};
  const min = toZoomLevel(manifest?.min_zoom);
  const max = toZoomLevel(manifest?.max_zoom);
  if (min !== null) range.minzoom = min;
  if (max !== null) range.maxzoom = max;
  if (range.minzoom !== undefined && range.maxzoom !== undefined && range.minzoom > range.maxzoom) {
    delete range.minzoom;
  }
  return range;
}

export function isEditableVectorStatus(vectorStatus) {
  return vectorStatus === 'ready' || vectorStatus === 'ready_empty';
}
