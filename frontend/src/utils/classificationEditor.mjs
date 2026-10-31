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

/**
 * 构建服务端保存响应分配的正式图斑 ID 映射（client-* → manual-*）。
 * 服务端规范化（classification_results.py _validate_save_payload）按提交
 * 顺序逐要素 append、几何不变形：响应 FC 与提交 FC 一一对应，既有 ID 保留、
 * client- 前缀或缺省的要素分配新 manual-*。竞态分支据此把正式 ID 同步回画布与历史，
 * 否则再次保存会把同一面当新面，破坏跨版本图斑追踪（2026-10-31 复审 P2）。
 * 长度不一致（理论上不可能）时不猜测，返回空 Map 保持现状。
 */
export function buildAssignedFeatureIdMap(submittedCollection, savedCollection) {
  const mapping = new Map();
  const submitted = submittedCollection?.features || [];
  const saved = savedCollection?.features || [];
  if (!submitted.length || submitted.length !== saved.length) return mapping;
  for (let index = 0; index < submitted.length; index += 1) {
    const before = submitted[index]?.properties?.feature_id;
    const after = saved[index]?.properties?.feature_id;
    if (typeof before === 'string' && before.startsWith('client-') && typeof after === 'string' && after !== before) {
      mapping.set(before, after);
    }
  }
  return mapping;
}

/**
 * 对 currentDrawJson() 风格的快照字符串应用 ID 映射（与
 * classificationEditorHistory.migrateIds 同一规则），用于把保存提交快照
 * 重写为正式 ID 版本后再登记为历史基线。
 */
export function rewriteFeatureCollectionJson(snapshotJson, idMap) {
  if (typeof snapshotJson !== 'string' || !(idMap instanceof Map) || idMap.size === 0) {
    return snapshotJson;
  }
  if (snapshotJson.indexOf('"feature_id"') === -1) return snapshotJson;
  try {
    const parsed = JSON.parse(snapshotJson);
    let changed = false;
    for (const feature of parsed.features || []) {
      const current = feature.properties?.feature_id;
      if (typeof current === 'string' && idMap.has(current)) {
        feature.properties.feature_id = idMap.get(current);
        changed = true;
      }
    }
    return changed ? JSON.stringify(parsed) : snapshotJson;
  } catch (_) {
    return snapshotJson;
  }
}

export function isEditableVectorStatus(vectorStatus) {
  return vectorStatus === 'ready' || vectorStatus === 'ready_empty';
}
