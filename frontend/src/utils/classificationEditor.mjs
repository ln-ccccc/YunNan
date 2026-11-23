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

export function basemapCoverageNotice(featureBounds, manifestBounds) {
  /* 底图覆盖提示（2026-11-23 底图覆盖缺口处置 B）：编辑器框选的是成果要素，
   * 当矿山落在激活底图金字塔覆盖之外时画布"正确地空白"，此前用户无从分辨
   * 是缺陷还是缺数据。这里按 [w,s,e,n] 判相交/包含，返回提示或 null。
   * 任一边界缺失（无要素/无 manifest）不打扰。 */
  if (!Array.isArray(featureBounds) || !Array.isArray(manifestBounds)) return null;
  const intersects = featureBounds[0] <= manifestBounds[2] && manifestBounds[0] <= featureBounds[2]
    && featureBounds[1] <= manifestBounds[3] && manifestBounds[1] <= featureBounds[3];
  if (!intersects) {
    return {
      level: 'warning',
      message: '当前矿山位于激活底图覆盖范围之外，地图不显示底图影像（矢量编辑与保存不受影响）',
    };
  }
  const inside = featureBounds[0] >= manifestBounds[0] && featureBounds[1] >= manifestBounds[1]
    && featureBounds[2] <= manifestBounds[2] && featureBounds[3] <= manifestBounds[3];
  if (inside) return null;
  return {
    level: 'info',
    message: '成果范围部分超出激活底图覆盖，视野边缘可能缺少底图影像',
  };
}

function ringVertexCount(ring) {
  /* 环的真实顶点数（去闭合重复点）：与 mapbox-gl-draw 内部不含闭合点的顶点下标一致 */
  if (!Array.isArray(ring) || ring.length === 0) return 0;
  const first = ring[0];
  const last = ring[ring.length - 1];
  const closed = Array.isArray(first) && Array.isArray(last)
    && first[0] === last[0] && first[1] === last[1] && ring.length > 1;
  return closed ? ring.length - 1 : ring.length;
}

/**
 * 本次删除顶点是否会让某个子面的外环塌陷（direct_select 真实删除入口的防护，
 * 2026-11-23 P1）。
 *
 * mapbox-gl-draw 1.5.1 的 DirectSelect.onTrash（删除按钮与 Delete/Backspace 键盘
 * 两条路都汇到这里）对选中顶点逐个 removeCoordinate：某环剩 <3 点时整个环被删，
 * 随后 isValid() 为假即 deleteFeature 整面——上游没有"最少顶点保护"。
 * Polygon 路径形如 "<环>.<顶点>"；MultiPolygon 是 MultiFeature，路径形如
 * "<子面>.<环>.<顶点>"。
 *
 * 只看各子面的外环（环序号 0）：本次选中的外环顶点按子面去重计数，
 * 「外环顶点数 − 本次删除数 < 3」即记一个 blocker。内环（洞）塌陷是合法操作，不拦。
 * 几何缺失或类型不支持时返回空数组（不拦，保持上游行为）。
 *
 * @param {object} geometry state.feature.toGeoJSON().geometry（标准 GeoJSON，环已闭合）
 * @param {string[]} selectedCoordPaths state.selectedCoordPaths
 * @returns {{ polygonIndex: number, vertexCount: number, removed: number, remaining: number }[]}
 */
export function vertexDeletionBlockers(geometry, selectedCoordPaths) {
  if (!geometry || (geometry.type !== 'Polygon' && geometry.type !== 'MultiPolygon')) return [];
  const isMulti = geometry.type === 'MultiPolygon';
  const polygons = isMulti ? geometry.coordinates : [geometry.coordinates];
  if (!Array.isArray(polygons)) return [];

  const removedByPolygon = new Map();
  for (const rawPath of Array.isArray(selectedCoordPaths) ? selectedCoordPaths : []) {
    if (typeof rawPath !== 'string') continue;
    const parts = rawPath.split('.');
    const [polygonIndex, ringIndex, vertexIndex] = isMulti
      ? [parseInt(parts[0], 10), parseInt(parts[1], 10), parseInt(parts[2], 10)]
      : [0, parseInt(parts[0], 10), parseInt(parts[1], 10)];
    if (![polygonIndex, ringIndex, vertexIndex].every(Number.isInteger)) continue;
    if (ringIndex !== 0) continue;
    const outerRing = Array.isArray(polygons[polygonIndex]) ? polygons[polygonIndex][0] : null;
    if (!Array.isArray(outerRing)) continue;
    // 下标越界时上游 removeCoordinate 的 splice 不删任何点，不计入本次删除数
    if (vertexIndex >= ringVertexCount(outerRing)) continue;
    const key = `${polygonIndex}.${vertexIndex}`;
    if (!removedByPolygon.has(key)) removedByPolygon.set(key, polygonIndex);
  }

  const removedCounts = new Map();
  for (const polygonIndex of removedByPolygon.values()) {
    removedCounts.set(polygonIndex, (removedCounts.get(polygonIndex) || 0) + 1);
  }
  const blockers = [];
  [...removedCounts.keys()].sort((a, b) => a - b).forEach((polygonIndex) => {
    const removed = removedCounts.get(polygonIndex);
    const vertexCount = ringVertexCount(polygons[polygonIndex][0]);
    const remaining = vertexCount - removed;
    if (remaining < 3) blockers.push({ polygonIndex, vertexCount, removed, remaining });
  });
  return blockers;
}

/**
 * 顶点删除被拦截时的提示文案（纯文本，便于无 DOM 测试）。
 * MultiPolygon 点明是第几个子面（序号从 1 起）。
 */
export function describeVertexDeletionBlockers(blockers, geometry) {
  const isMulti = geometry?.type === 'MultiPolygon';
  const detail = (blockers || [])
    .map((blocker) => {
      const target = isMulti ? `第 ${blocker.polygonIndex + 1} 个子面` : '该面';
      return `本次删除后${target}只剩 ${blocker.remaining} 个顶点（最少 3 个）`;
    })
    .join('；');
  return `${detail}，会连带删除整个面；如需删除整个面，请按 Esc 退出顶点编辑后单击选中面再删除`;
}

/**
 * 带顶点删除防护的 direct_select 模式工厂（官方扩展点：
 * new MapboxDraw({ modes: { ...MapboxDraw.modes, direct_select: 自定义对象 } })）。
 * 返回的对象保留上游模式的全部方法，只接管 onTrash——删除按钮与键盘
 * Delete/Backspace 两条路都必经此处，不再依赖地图容器的点击捕获（键盘路完全绕过）。
 *
 * 模式方法被调用时 this 是 mapbox-gl-draw 的模式处理器（带 fireActionable /
 * deleteFeature / changeMode 等），因此放行时用 call(this) 复用上游逻辑。
 *
 * @param {object} baseDirectSelect MapboxDraw.modes.direct_select
 * @param {(blockers: object[], geometry: object) => void} onBlocked 拦截时的提示回调
 */
export function createGuardedDirectSelect(baseDirectSelect, onBlocked) {
  return {
    ...baseDirectSelect,
    onTrash(state) {
      const selectedCoordPaths = Array.isArray(state?.selectedCoordPaths) ? state.selectedCoordPaths : [];
      // 无选中顶点：上游会发一次空的 draw.update（被 handleDrawUpdate 误记为一笔
      // 编辑并递增 editGeneration），这里只同步按钮可用态后短路
      if (!selectedCoordPaths.length) {
        this.fireActionable(state);
        return;
      }
      const geometry = state?.feature?.toGeoJSON?.()?.geometry ?? null;
      const blockers = vertexDeletionBlockers(geometry, selectedCoordPaths);
      if (blockers.length) {
        if (typeof onBlocked === 'function') onBlocked(blockers, geometry);
        this.fireActionable(state);
        return;
      }
      return baseDirectSelect.onTrash.call(this, state);
    },
  };
}

function coordinatesBounds(coordinateLists) {
  /* 任意深度坐标数组的 [w, s, e, n] 包围盒；无可识别位置时返回 null */
  let bounds = null;
  const visit = (value) => {
    if (!Array.isArray(value)) return;
    if (value.length >= 2 && Number.isFinite(value[0]) && Number.isFinite(value[1])) {
      bounds = bounds
        ? [
          Math.min(bounds[0], value[0]),
          Math.min(bounds[1], value[1]),
          Math.max(bounds[2], value[0]),
          Math.max(bounds[3], value[1]),
        ]
        : [value[0], value[1], value[0], value[1]];
      return;
    }
    value.forEach(visit);
  };
  coordinateLists.forEach(visit);
  return bounds;
}

function isValidBounds(value) {
  if (!Array.isArray(value) || value.length !== 4 || !value.every(Number.isFinite)) return false;
  const [west, south, east, north] = value;
  return !(west < -180 || east > 180 || south < -90 || north > 90 || west >= east || south >= north);
}

/**
 * 编辑器定位与底图覆盖判定共用的关注范围（E1，2026-11-23），优先级：
 * 图斑范围 → ROI（矿山边界）范围 → manifest 范围。
 * ready_empty 成果没有图斑时，此前直接回退到整张底图范围（看到整个云南，
 * 找不到矿山在哪）；后端详情已返回 roi_geometry，画布上也有 result-roi-outline 图层。
 *
 * @param {object} featureCollection result.current_feature_collection
 * @param {object} roiGeometry GeoJSON Polygon / MultiPolygon 几何
 * @param {number[]} manifestBounds [w, s, e, n]（非法时忽略）
 * @returns {number[]|null} [w, s, e, n]，三级都缺失时为 null
 */
export function resolveFocusBounds(featureCollection, roiGeometry, manifestBounds) {
  const featureBounds = coordinatesBounds(
    (featureCollection?.features || []).map((feature) => feature?.geometry?.coordinates),
  );
  if (featureBounds) return featureBounds;
  const roiBounds = coordinatesBounds([roiGeometry?.coordinates]);
  if (roiBounds) return roiBounds;
  return isValidBounds(manifestBounds) ? [...manifestBounds] : null;
}

/**
 * 草稿导出文件名（E2-e）：与服务端 classification-result-<id>-revision-<n>.geojson
 * 同一命名族，draft-v<n>-unsaved 标明"基于 Vn 的未保存草稿"。
 */
export function draftExportFilename(resultId, revisionNo) {
  return `classification-result-${resultId}-draft-v${revisionNo}-unsaved.geojson`;
}
