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
 * 顶点与"中点"标记的样式 id 与圆半径。命中判定（nearestPointTarget）与编辑器的
 * drawStyles() 共用同一组数值：改半径时两处同时生效，不会各说各话。
 */
export const DRAW_POINT_STYLES = {
  vertex: { id: 'gl-draw-vertex-outer', radius: 7 },
  midpoint: { id: 'gl-draw-midpoint', radius: 3 },
};

/**
 * 就近命中：在各自的命中半径内，取离点击点最近的顶点或中点；距离只差亚像素时
 * 顶点优先。candidates 为 [{ kind: 'vertex' | 'midpoint', x, y, ... }]（画布像素
 * 坐标），hitRadius(kind) 给出该类目标的命中半径（像素）。没有命中返回 null。
 *
 * midpointClearance（像素，可选）：离任一顶点比它还近的中点不参与命中。传顶点圆的
 * 半径，含义是"中点标记的中心被顶点圆盖住时点不到"——能点到的目标和看得见的
 * 目标一致。
 *
 * 为什么要自己择近（2026-11-24）：mapbox-gl-draw 的 featuresAt 对查询结果按
 * properties.id 去重，而顶点与中点要素都没有 id——同一像素上的多个点只保留
 * "渲染在最上面的那一个"（官方主题与本项目样式里中点图层都在顶点图层之上）。
 * 于是相邻顶点在屏幕上靠得近时：点顶点会被判成点中点，而 onMidpoint 在鼠标
 * 按下那一刻就往面上加一个顶点；几个顶点圆重叠时选中的是绘制顺序靠后的那个，
 * 不是离点击最近的——拖一下就改错了顶点。
 *
 * 择近之后（顶点圆半径 7、中点 3、点击缓冲 2）：点在顶点上永远选中顶点；相邻
 * 顶点相距不超过 14px 时中点不参与，怎么点都不会多出顶点；相距更远时，点在
 * 两个顶点圆之间露出来的小圆点上才新增顶点。
 */
export function pickNearestPointTarget(candidates, point, hitRadius, { midpointClearance = 0 } = {}) {
  const TIE_EPSILON = 0.5;
  const eligible = (candidates || []).filter((candidate) => ['vertex', 'midpoint'].includes(candidate.kind));
  const vertices = eligible.filter((candidate) => candidate.kind === 'vertex');
  const actionable = eligible.filter((candidate) => (
    candidate.kind === 'vertex'
    || midpointClearance <= 0
    || vertices.every((vertex) => Math.hypot(vertex.x - candidate.x, vertex.y - candidate.y) >= midpointClearance)
  ));
  let best = null;
  for (const candidate of actionable) {
    const distance = Math.hypot(candidate.x - point.x, candidate.y - point.y);
    if (distance > hitRadius(candidate.kind)) continue;
    const nearer = best === null || distance < best.distance - TIE_EPSILON;
    const tieVertexWins = best !== null
      && Math.abs(distance - best.distance) <= TIE_EPSILON
      && candidate.kind === 'vertex'
      && best.kind !== 'vertex';
    if (nearer || tieVertexWins) best = { ...candidate, distance };
  }
  return best;
}

/**
 * 把经纬度几何投影到画布后用鞋带公式算屏幕面积（px²）：外环减去洞，MultiPolygon
 * 累加。只用来比较大小，不做任何地理换算。
 */
function projectedArea(map, geometry) {
  const polygons = geometry.type === 'MultiPolygon' ? geometry.coordinates : [geometry.coordinates];
  let total = 0;
  for (const rings of polygons) {
    rings.forEach((ring, ringIndex) => {
      if (!Array.isArray(ring)) return;
      let doubled = 0;
      for (let index = 0; index + 1 < ring.length; index += 1) {
        const from = map.project(ring[index]);
        const to = map.project(ring[index + 1]);
        doubled += from.x * to.y - to.x * from.y;
      }
      const area = Math.abs(doubled / 2);
      total += ringIndex === 0 ? area : -area;
    });
  }
  return total;
}

/**
 * 用与库相同的查询范围（同一个点击缓冲盒、同一组绘图图层）重新整理一次点击的命中结果。
 *
 * 为什么必须自己整理（2026-11-24 通读绘制库时实锤）：mapbox-gl-draw 1.5.1 的
 * featuresAt 想要的顺序是"点在前，面按面积从小到大"（lib/sort_features.js，
 * 注释写着 Sort in the order above, then sort polygons by area ascending）。但它给
 * 面积函数传的是 Feature 而不是几何——@mapbox/geojson-area 0.2.2 的 geometry()
 * 只认 Polygon / MultiPolygon 等几何类型，对 {type:'Feature', …} 返回 undefined，
 * 于是比较结果恒为 NaN，排序从未生效，命中的永远是瓦片里排在前面的那个面。
 * 实测：新画的小面叠在大面上，点小面选中的是大面；真实成果默认视野下 57 个图斑
 * 只有 31 个能单独点中。点那一侧同理：顶点与中点要素都没有 id，featuresAt 的去重
 * 把同一像素上的点只留"渲染在最上面的那一个"（见 pickNearestPointTarget）。
 *
 * 整理规则：
 * - 点（顶点 / 中点）：择近；
 * - 面：恰好有一个面把点击点本身包含在内就选它；否则（互相重叠、或只是擦到边）
 *   取面积最小的——小图斑本来就难点，不能再让大图斑把它挡住。
 *
 * 返回 { points, polygons, preferredPolygon, buffer }；preferredPolygon 只在同时命中
 * 两个以上的面时非空（只命中一个时库选的就是它）。查询无法进行时返回 null。
 */
function queryDrawHits(mode, event) {
  try {
    const map = mode.map;
    const config = mode.drawConfig || {};
    const point = event && event.point;
    if (!map || !Array.isArray(config.styles) || !point) return null;
    const layerIds = config.styles.map((style) => style.id);
    if (!layerIds.length) return null;
    const buffer = Number(config.clickBuffer) || 0;
    const hits = map.queryRenderedFeatures(
      [[point.x - buffer, point.y - buffer], [point.x + buffer, point.y + buffer]],
      { layers: layerIds },
    );
    const points = [];
    const polygons = [];
    const seenPolygons = new Set();
    for (const feature of hits) {
      const properties = feature.properties || {};
      const geometry = feature.geometry;
      if (!geometry || !Array.isArray(geometry.coordinates)) continue;
      if (properties.meta === 'vertex' || properties.meta === 'midpoint') {
        const projected = map.project(geometry.coordinates);
        points.push({ kind: properties.meta, feature, x: projected.x, y: projected.y });
      } else if (properties.meta === 'feature' && ['Polygon', 'MultiPolygon'].includes(geometry.type)) {
        // 同一个面会同时出现在填充层与描边层：只留第一次
        if (seenPolygons.has(properties.id)) continue;
        seenPolygons.add(properties.id);
        polygons.push({ kind: 'polygon', feature, area: projectedArea(map, geometry) });
      }
    }
    polygons.sort((left, right) => left.area - right.area);
    let preferredPolygon = null;
    // "点击点本身在哪个面里"只能问填充图层：描边图层的命中带容差，挨着点击点的一条边也会
    // 被算成"点在它里面"——真实成果上点道路面，贴着它的建筑面就这样被当成也包含点击点，
    // 规则退回"取最小的面"，选中的成了建筑面（2026-11-24 真实编辑测试抓到）
    const fillLayerIds = config.styles.filter((style) => style.type === 'fill').map((style) => style.id);
    if (polygons.length > 1 && fillLayerIds.length) {
      const insideIds = new Set(
        map.queryRenderedFeatures([point.x, point.y], { layers: fillLayerIds })
          .filter((feature) => feature.properties && feature.properties.meta === 'feature')
          .map((feature) => feature.properties.id),
      );
      const inside = polygons.filter((candidate) => insideIds.has(candidate.feature.properties.id));
      preferredPolygon = (inside.length === 1 ? inside : polygons)[0].feature;
    } else if (polygons.length > 1) {
      preferredPolygon = polygons[0].feature;
    }
    return { points, polygons, preferredPolygon, buffer };
  } catch (_) {
    // 查询或投影异常时返回 null：调用方退回库自己的命中结果，不让一次点击失效
    return null;
  }
}

function nearestPointTarget(mode, event) {
  const hits = queryDrawHits(mode, event);
  if (!hits) return null;
  const nearest = pickNearestPointTarget(
    hits.points,
    event.point,
    (kind) => DRAW_POINT_STYLES[kind].radius + hits.buffer,
    { midpointClearance: DRAW_POINT_STYLES.vertex.radius },
  );
  return nearest ? nearest.feature : null;
}

function isPointTarget(event) {
  const meta = event && event.featureTarget && event.featureTarget.properties ? event.featureTarget.properties.meta : null;
  return meta === 'vertex' || meta === 'midpoint';
}

/** 把事件目标换成"应该选中的那个面"（规则见 queryDrawHits）。库已经选中点目标、或只命中一个面时不动。 */
function preferSmallestPolygon(mode, event) {
  if (!event || isPointTarget(event)) return;
  const hits = queryDrawHits(mode, event);
  if (hits && hits.preferredPolygon) event.featureTarget = hits.preferredPolygon;
}

/**
 * 带顶点删除防护与正确命中的 direct_select 模式工厂（官方扩展点：
 * new MapboxDraw({ modes: { ...MapboxDraw.modes, direct_select: 自定义对象 } })）。
 * 返回的对象保留上游模式的全部方法，只接管这几处：
 * - onTrash——删除按钮与键盘 Delete/Backspace 两条路都必经此处；
 * - onMouseDown——按下时把目标换成离点击最近的顶点/中点（见 pickNearestPointTarget），
 *   附近没有点目标时换成应当选中的面（见 queryDrawHits），随后仍交给上游的
 *   onMouseDown（顶点→onVertex、面主体→onFeature、中点→onMidpoint）；
 * - onClick / onTap——抬起时同样换成应当选中的面：顶点模式下点另一个（更小的）面
 *   要退回普通选择，而不是被当成点了当前面；
 * - onMidpoint——点中点加了顶点之后，把新顶点同步为库里的选中坐标（上游只改了
 *   state.selectedCoordPaths）。
 *
 * 模式方法被调用时 this 是 mapbox-gl-draw 的模式处理器（带 fireActionable /
 * deleteFeature / changeMode / map / drawConfig 等），因此放行时用 call(this) 复用上游逻辑。
 *
 * @param {object} baseDirectSelect MapboxDraw.modes.direct_select
 * @param {(blockers: object[], geometry: object) => void} onBlocked 拦截时的提示回调
 */
export function createGuardedDirectSelect(baseDirectSelect, onBlocked) {
  return {
    ...baseDirectSelect,
    onMouseDown(state, e) {
      const nearest = nearestPointTarget(this, e);
      if (nearest) e.featureTarget = nearest;
      else preferSmallestPolygon(this, e);
      return baseDirectSelect.onMouseDown.call(this, state, e);
    },
    onClick(state, e) {
      preferSmallestPolygon(this, e);
      return baseDirectSelect.onClick.call(this, state, e);
    },
    onTap(state, e) {
      preferSmallestPolygon(this, e);
      return baseDirectSelect.onTap.call(this, state, e);
    },
    onMidpoint(state, e) {
      const result = baseDirectSelect.onMidpoint.call(this, state, e);
      // 上游只改了 state.selectedCoordPaths，没有同步库里的选中坐标（getSelectedPoints()
      // 仍为空）：新顶点在界面上是选中的，此刻按 Delete 却会先弹"请先点击选中某个顶点"
      // 的引导，随后又把它删掉。这里补上同步，删除防护与引导提示看到的才是同一个状态
      this.setSelectedCoordinates(this.pathsToCoordinates(state.featureId, state.selectedCoordPaths));
      return result;
    },
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

/**
 * 命中规则修正后的 simple_select 模式工厂（官方扩展点同上）。上游的 onMouseDown /
 * onClick / onTap 全部保留，只在前面把目标换成应当选中的面（见 queryDrawHits）：
 * 库的"小面优先"从未生效，叠在一起或贴着边的几个图斑里，点小图斑选中的常常是大图斑。
 *
 * @param {object} baseSimpleSelect MapboxDraw.modes.simple_select
 */
export function createGuardedSimpleSelect(baseSimpleSelect) {
  return {
    ...baseSimpleSelect,
    onMouseDown(state, e) {
      preferSmallestPolygon(this, e);
      return baseSimpleSelect.onMouseDown.call(this, state, e);
    },
    onClick(state, e) {
      preferSmallestPolygon(this, e);
      return baseSimpleSelect.onClick.call(this, state, e);
    },
    onTap(state, e) {
      preferSmallestPolygon(this, e);
      return baseSimpleSelect.onTap.call(this, state, e);
    },
  };
}

/** 绘制新模式（本编辑器只开放画面） */
export const SKETCH_MODE = 'draw_polygon';

/**
 * 绘制中的"删除"改为退回上一个顶点的 draw_polygon 模式工厂。
 *
 * 上游 onTrash 一律丢弃整个草稿（与 Esc 相同）。而 Delete/Backspace 在绘制中
 * 的通行含义是"退回刚放的点"（QGIS、ArcGIS 同），12b79de 让画布上的这两个键
 * 生效之后，按一下就丢掉画了一半的面，太容易误伤。
 *
 * 草稿状态（上游 onSetup / clickAnywhere）：已放置 k 个顶点时
 * state.currentVertexPosition === k，外环坐标为 [p0 … p(k-1), 跟随鼠标的点]。
 * 这里把 currentVertexPosition 减一并摘掉最后那个已放置的点，"跟随鼠标的点"
 * 顺位补上，下一次 mousemove 仍写 0.(k-1)，状态自洽。
 * 不用 polygon.removeCoordinate：它会把删除后不足 3 个点的环整个删掉（onStop
 * 靠这个丢弃草稿），退回到只剩一两个点时会连别的点一起丢。
 * 一个点都没放时沿用上游行为：丢弃（空）草稿，回到选择模式。
 */
export function createStepBackDrawPolygon(baseDrawPolygon) {
  return {
    ...baseDrawPolygon,
    onTrash(state) {
      if (!state || !(state.currentVertexPosition > 0)) {
        return baseDrawPolygon.onTrash.call(this, state);
      }
      state.currentVertexPosition -= 1;
      state.polygon.coordinates[0].splice(state.currentVertexPosition, 1);
      state.polygon.changed();
      return undefined;
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
