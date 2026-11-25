import test from 'node:test';
import assert from 'node:assert/strict';
import MapboxDraw from '@mapbox/mapbox-gl-draw';

import {
  DRAW_POINT_STYLES,
  basemapCoverageNotice,
  buildAssignedFeatureIdMap,
  buildEditableFeatureCollection,
  createGuardedDirectSelect,
  createGuardedSimpleSelect,
  createStepBackDrawPolygon,
  describeVertexDeletionBlockers,
  draftExportFilename,
  isEditableVectorStatus,
  pickNearestPointTarget,
  resolveFocusBounds,
  resolveSecureTileTemplate,
  resolveTileZoomRange,
  rewriteFeatureCollectionJson,
  vertexDeletionBlockers,
} from '../src/utils/classificationEditor.mjs';

const SERVER_FEATURE_COLLECTION = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      id: 'draw-internal-id',
      properties: {
        feature_id: 'auto-7-0-0001',
        class_code: 0,
        class_name: 'grassland',
        source: 'auto',
        result_id: 7,
        revision_no: 0,
        injected: 'must-not-be-submitted',
      },
      geometry: {
        type: 'Polygon',
        coordinates: [[[100, 30], [101, 30], [101, 31], [100, 31], [100, 30]]],
      },
    },
  ],
};

test('buildEditableFeatureCollection strips all server-only and draw-only fields', () => {
  const result = buildEditableFeatureCollection(SERVER_FEATURE_COLLECTION);

  assert.deepEqual(result, {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        properties: {
          feature_id: 'auto-7-0-0001',
          class_code: 0,
        },
        geometry: SERVER_FEATURE_COLLECTION.features[0].geometry,
      },
    ],
  });

  result.features[0].geometry.coordinates[0][0][0] = 120;
  assert.equal(SERVER_FEATURE_COLLECTION.features[0].geometry.coordinates[0][0][0], 100);
});

test('buildEditableFeatureCollection omits empty temporary ids and rejects malformed draw data', () => {
  const withoutId = structuredClone(SERVER_FEATURE_COLLECTION);
  withoutId.features[0].properties = { class_code: 2, feature_id: '' };
  assert.deepEqual(buildEditableFeatureCollection(withoutId).features[0].properties, { class_code: 2 });

  const unsupportedGeometry = structuredClone(SERVER_FEATURE_COLLECTION);
  unsupportedGeometry.features[0].geometry = { type: 'LineString', coordinates: [[100, 30], [101, 31]] };
  assert.throws(() => buildEditableFeatureCollection(unsupportedGeometry), /Polygon/);

  const invalidClass = structuredClone(SERVER_FEATURE_COLLECTION);
  invalidClass.features[0].properties.class_code = 255;
  assert.throws(() => buildEditableFeatureCollection(invalidClass), /class_code/);
});

test('resolveSecureTileTemplate accepts only the API template bound to the authenticated backend origin', () => {
  assert.equal(
    resolveSecureTileTemplate(
      '/api/projects/17/map-resources/29/tiles/{z}/{x}/{y}.png',
      'https://geoview.example.test:5008/',
    ),
    'https://geoview.example.test:5008/api/projects/17/map-resources/29/tiles/{z}/{x}/{y}.png',
  );
  assert.equal(
    resolveSecureTileTemplate(
      'https://untrusted.example.test/tiles/{z}/{x}/{y}.png',
      'https://geoview.example.test:5008/',
    ),
    null,
  );
  assert.equal(
    resolveSecureTileTemplate(
      '/tiles/projects/17/29/{z}/{x}/{y}.png',
      'https://geoview.example.test:5008/',
    ),
    null,
  );
});

test('isEditableVectorStatus permits only ready result states', () => {
  assert.equal(isEditableVectorStatus('ready'), true);
  assert.equal(isEditableVectorStatus('ready_empty'), true);
  assert.equal(isEditableVectorStatus('vector_failed'), false);
  assert.equal(isEditableVectorStatus('pending'), false);
});

test('resolveTileZoomRange maps declared manifest zooms onto source minzoom/maxzoom', () => {
  assert.deepEqual(resolveTileZoomRange({ min_zoom: 8, max_zoom: 15 }), { minzoom: 8, maxzoom: 15 });
  // 边界值 0 与 22 是合法级别（min_zoom=0 常见于全量金字塔）
  assert.deepEqual(resolveTileZoomRange({ min_zoom: 0, max_zoom: 22 }), { minzoom: 0, maxzoom: 22 });
  // 字符串数字宽容接受
  assert.deepEqual(resolveTileZoomRange({ min_zoom: '5', max_zoom: '14' }), { minzoom: 5, maxzoom: 14 });
});

test('resolveTileZoomRange keeps sources unrestricted when manifest zooms are missing or invalid', () => {
  // 未声明（null/undefined/缺失）不得经 Number(null)=0 误判为 0 级
  assert.deepEqual(resolveTileZoomRange({ min_zoom: null, max_zoom: null }), {});
  assert.deepEqual(resolveTileZoomRange({}), {});
  assert.deepEqual(resolveTileZoomRange(undefined), {});
  // 非整数与越界值忽略
  assert.deepEqual(resolveTileZoomRange({ min_zoom: 5.5, max_zoom: 15 }), { maxzoom: 15 });
  assert.deepEqual(resolveTileZoomRange({ min_zoom: 8, max_zoom: 23 }), { minzoom: 8 });
  assert.deepEqual(resolveTileZoomRange({ min_zoom: -1, max_zoom: 15 }), { maxzoom: 15 });
  // min>max 属异常清单：丢 min 保留 max（防越级 404 是主诉求）
  assert.deepEqual(resolveTileZoomRange({ min_zoom: 12, max_zoom: 6 }), { maxzoom: 6 });
});

const featureWithId = (featureId) => ({
  type: 'Feature',
  properties: { class_code: 1, ...(featureId === undefined ? {} : { feature_id: featureId }) },
  geometry: { type: 'Polygon', coordinates: [[[100, 30], [101, 30], [101, 31], [100, 31], [100, 30]]] },
});

// 竞态 ID 同步（2026-10-31 复审 P2）：服务端按提交顺序逐要素规范化、既有 ID
// 保留、client- 前缀或缺省分配新 manual-*，响应 FC 与提交 FC 一一对应。
test('buildAssignedFeatureIdMap pairs client ids with server-assigned ids in order', () => {
  const submitted = {
    type: 'FeatureCollection',
    features: [featureWithId('auto-142-1'), featureWithId('client-abc'), featureWithId('client-def')],
  };
  const saved = {
    type: 'FeatureCollection',
    features: [featureWithId('auto-142-1'), featureWithId('manual-142-2'), featureWithId('manual-142-3')],
  };
  const mapping = buildAssignedFeatureIdMap(submitted, saved);
  assert.equal(mapping.size, 2);
  assert.equal(mapping.get('client-abc'), 'manual-142-2');
  assert.equal(mapping.get('client-def'), 'manual-142-3');

  // 既有 ID 被服务端保留（前后一致）→ 不进入映射
  assert.deepEqual(buildAssignedFeatureIdMap(
    { type: 'FeatureCollection', features: [featureWithId('auto-142-1')] },
    { type: 'FeatureCollection', features: [featureWithId('auto-142-1')] },
  ), new Map());
  // 提交时缺省 feature_id 的要素无法在画布定位替换 → 不猜测
  assert.deepEqual(buildAssignedFeatureIdMap(
    { type: 'FeatureCollection', features: [featureWithId(undefined)] },
    { type: 'FeatureCollection', features: [featureWithId('manual-142-9')] },
  ), new Map());
  // 数量不一致（理论上不可能）时不按序猜测
  assert.deepEqual(buildAssignedFeatureIdMap(
    { type: 'FeatureCollection', features: [featureWithId('client-abc'), featureWithId('auto-1')] },
    { type: 'FeatureCollection', features: [featureWithId('manual-142-2')] },
  ), new Map());
  assert.deepEqual(buildAssignedFeatureIdMap(undefined, saved), new Map());
});

test('rewriteFeatureCollectionJson rewrites only mapped ids and keeps unrelated snapshots intact', () => {
  const mapping = new Map([['client-abc', 'manual-142-2']]);
  const snapshot = JSON.stringify({ type: 'FeatureCollection', features: [featureWithId('auto-142-1'), featureWithId('client-abc')] });
  const rewritten = rewriteFeatureCollectionJson(snapshot, mapping);
  const parsed = JSON.parse(rewritten);
  assert.equal(parsed.features[0].properties.feature_id, 'auto-142-1');
  assert.equal(parsed.features[1].properties.feature_id, 'manual-142-2');

  // 无映射/无匹配时原样返回（字符串同一性保持，避免无谓重排）
  assert.equal(rewriteFeatureCollectionJson(snapshot, new Map()), snapshot);
  assert.equal(rewriteFeatureCollectionJson(undefined, mapping), undefined);
  const noMatch = JSON.stringify({ type: 'FeatureCollection', features: [featureWithId('auto-142-1')] });
  assert.equal(rewriteFeatureCollectionJson(noMatch, mapping), noMatch);
});

test('basemapCoverageNotice classifies feature bounds against manifest bounds', () => {
  const manifest = [100.5, 25.9, 101.4, 26.3];
  // 完全在覆盖内：不提示
  assert.equal(basemapCoverageNotice([100.7, 26.0, 100.8, 26.1], manifest), null);
  // 完全在覆盖外（南缘外）：warning
  const outside = basemapCoverageNotice([100.70, 25.33, 100.71, 25.34], manifest);
  assert.equal(outside.level, 'warning');
  assert.match(outside.message, /覆盖范围之外/);
  // 部分相交：info
  const partial = basemapCoverageNotice([100.7, 25.8, 100.8, 26.1], manifest);
  assert.equal(partial.level, 'info');
  // 任一边界缺失：不提示
  assert.equal(basemapCoverageNotice(null, manifest), null);
  assert.equal(basemapCoverageNotice([100.7, 26.0, 100.8, 26.1], null), null);
});

// ─── 关注范围（E1，2026-11-23）：图斑 → ROI → manifest ───────────────────────

const collectionOf = (...geometries) => ({
  type: 'FeatureCollection',
  features: geometries.map((geometry, index) => ({
    type: 'Feature',
    id: `f${index}`,
    properties: { class_code: 0 },
    geometry,
  })),
});
const squareAt = (x, y, size) => ({
  type: 'Polygon',
  coordinates: [[[x, y], [x + size, y], [x + size, y + size], [x, y + size], [x, y]]],
});
const rounded = (bounds) => bounds.map((value) => Number(value.toFixed(4)));

test('resolveFocusBounds prefers feature bounds, then ROI bounds, then manifest bounds', () => {
  const roi = squareAt(100.0, 25.0, 0.2);
  const manifest = [99.5, 24.5, 100.5, 25.5];
  const features = collectionOf(squareAt(100.1, 25.1, 0.01), squareAt(100.15, 25.15, 0.01));
  const expectedFeatureBounds = [100.1, 25.1, 100.16, 25.16];

  // 有图斑：只看图斑
  assert.deepEqual(rounded(resolveFocusBounds(features, roi, manifest)), expectedFeatureBounds);
  // ready_empty（没有图斑）但有 ROI：按 ROI 定位，不再回退到整张底图
  for (const empty of [{ type: 'FeatureCollection', features: [] }, collectionOf(), undefined]) {
    assert.deepEqual(rounded(resolveFocusBounds(empty, roi, manifest)), [100, 25, 100.2, 25.2]);
  }
  // 图斑要素缺几何时等同没有图斑
  const geometryless = { type: 'FeatureCollection', features: [{ type: 'Feature', properties: {}, geometry: null }] };
  assert.deepEqual(rounded(resolveFocusBounds(geometryless, roi, manifest)), [100, 25, 100.2, 25.2]);
  // MultiPolygon ROI
  const multiRoi = {
    type: 'MultiPolygon',
    coordinates: [squareAt(100, 25, 0.1).coordinates, squareAt(100.3, 25.3, 0.1).coordinates],
  };
  assert.deepEqual(rounded(resolveFocusBounds(undefined, multiRoi, manifest)), [100, 25, 100.4, 25.4]);
  // 既无图斑也无 ROI：退到 manifest，返回副本而不是同一引用
  const fromManifest = resolveFocusBounds(collectionOf(), null, manifest);
  assert.deepEqual(fromManifest, manifest);
  assert.notEqual(fromManifest, manifest);
  // 三级都缺失 / manifest 非法：null
  assert.equal(resolveFocusBounds(collectionOf(), null, null), null);
  assert.equal(resolveFocusBounds(undefined, undefined, undefined), null);
  assert.equal(resolveFocusBounds(collectionOf(), null, [100.5, 25.5, 100.0, 25.0]), null, 'west >= east 非法');
  assert.equal(resolveFocusBounds(collectionOf(), null, [100, 25, 101]), null, '长度不是 4');
  // ROI 缺坐标时退到 manifest；ROI 不合法但图斑存在时不受影响
  assert.deepEqual(resolveFocusBounds(collectionOf(), { type: 'Polygon' }, manifest), manifest);
  assert.deepEqual(rounded(resolveFocusBounds(features, { type: 'Polygon' }, null)), expectedFeatureBounds);
});

test('ready_empty results still get a basemap coverage notice through their ROI', () => {
  // E1 的用户可见结果：成果没有图斑时关注范围此前为空，覆盖提示恒为 null
  const manifest = [100.5, 25.9, 101.4, 26.3];
  const empty = { type: 'FeatureCollection', features: [] };
  const outside = basemapCoverageNotice(
    resolveFocusBounds(empty, squareAt(100.0, 25.0, 0.2), manifest),
    manifest,
  );
  assert.equal(outside.level, 'warning');
  const inside = basemapCoverageNotice(
    resolveFocusBounds(empty, squareAt(100.6, 25.95, 0.3), manifest),
    manifest,
  );
  assert.equal(inside, null);
});

test('draftExportFilename marks the unsaved draft and its base revision', () => {
  // 与服务端 classification-result-<id>-revision-<n>.geojson 同一命名族
  assert.equal(draftExportFilename(142, 3), 'classification-result-142-draft-v3-unsaved.geojson');
});

// ─── 顶点删除防护（A1，2026-11-23 P1）────────────────────────────────────────
// 说明：原 polygonVertexCount 及其测试已删除。该函数只数"面当前的顶点数"，
// 看不到本次 Shift 选了几个顶点，且对 MultiPolygon 把首个子面的环数当成顶点数。
// 防护改挂到 direct_select.onTrash 真实入口，判定逻辑由 vertexDeletionBlockers 承担。
// 下面是替代它的新断言：旧用例的覆盖（闭合环 / 开放环 / 空环 / 单点环 / 空输入）
// 都有等价断言，并补上两条旧缺陷的回归用例。

const convexRing = (count) => Array.from({ length: count }, (_, index) => {
  const angle = (2 * Math.PI * index) / count;
  return [Math.round(Math.cos(angle) * 1e6) / 1e6, Math.round(Math.sin(angle) * 1e6) / 1e6];
});
const smallHoleRing = () => convexRing(3).map(([x, y]) => [x / 10, y / 10]);
const closedPolygon = (vertices) => ({ type: 'Polygon', coordinates: [[...vertices, vertices[0]]] });
const multiPolygonOf = (...polygons) => ({
  type: 'MultiPolygon',
  coordinates: polygons.map((polygon) => polygon.coordinates),
});
const triangle = closedPolygon(convexRing(3));
const square = closedPolygon(convexRing(4));

test('vertexDeletionBlockers blocks removing any vertex of a triangle', () => {
  for (const index of [0, 1, 2]) {
    assert.deepEqual(
      vertexDeletionBlockers(triangle, [`0.${index}`]),
      [{ polygonIndex: 0, vertexCount: 3, removed: 1, remaining: 2 }],
      `删除顶点 ${index} 应被拦截`,
    );
  }
});

test('vertexDeletionBlockers allows one vertex off a quadrilateral but blocks a Shift multi-selection', () => {
  // 开放环（未闭合）与闭合环计数一致——等价于旧用例的"闭合三角 3 / 开放四边形 4"
  assert.deepEqual(vertexDeletionBlockers({ type: 'Polygon', coordinates: [convexRing(4)] }, ['0.2']), []);
  assert.deepEqual(vertexDeletionBlockers(square, ['0.2']), []);
  // 缺陷①回归：四边形 Shift 选中两个顶点 → 剩 2 个 → 上游会删整面
  assert.deepEqual(vertexDeletionBlockers(square, ['0.0', '0.2']), [
    { polygonIndex: 0, vertexCount: 4, removed: 2, remaining: 2 },
  ]);
  assert.equal(vertexDeletionBlockers(square, ['0.0', '0.1', '0.3']).length, 1);
  // 同一路径重复出现只算一次
  assert.deepEqual(vertexDeletionBlockers(square, ['0.1', '0.1']), []);
});

test('vertexDeletionBlockers allows removing two vertices from a pentagon but not three', () => {
  const pentagon = closedPolygon(convexRing(5));
  assert.deepEqual(vertexDeletionBlockers(pentagon, ['0.0', '0.3']), []);
  const [blocker] = vertexDeletionBlockers(pentagon, ['0.0', '0.2', '0.4']);
  assert.deepEqual(blocker, { polygonIndex: 0, vertexCount: 5, removed: 3, remaining: 2 });
});

test('vertexDeletionBlockers ignores holes because a collapsing hole is a legal edit', () => {
  const hole = smallHoleRing();
  const triangleWithHole = {
    type: 'Polygon',
    coordinates: [[...convexRing(3), convexRing(3)[0]], [...hole, hole[0]]],
  };
  const quadWithHole = {
    type: 'Polygon',
    coordinates: [[...convexRing(4), convexRing(4)[0]], [...hole, hole[0]]],
  };
  // 只删洞的顶点（环序号 1）：三角形的洞删光也不拦
  assert.deepEqual(vertexDeletionBlockers(triangleWithHole, ['1.0']), []);
  assert.deepEqual(vertexDeletionBlockers(triangleWithHole, ['1.0', '1.1', '1.2']), []);
  // 外环与洞同时选中：只按外环计算
  assert.deepEqual(vertexDeletionBlockers(triangleWithHole, ['0.1', '1.2']), [
    { polygonIndex: 0, vertexCount: 3, removed: 1, remaining: 2 },
  ]);
  assert.deepEqual(vertexDeletionBlockers(quadWithHole, ['0.1', '1.0']), []);
});

test('vertexDeletionBlockers handles MultiPolygon sub-polygon paths', () => {
  const geometry = multiPolygonOf(
    closedPolygon(convexRing(3)),
    closedPolygon(convexRing(4)),
    closedPolygon(convexRing(3)),
  );
  // 第 1、3 个子面是三角面：删任一顶点后 MultiFeature.isValid() 为假，
  // 上游会把整个 MultiPolygon（连同其他子面）一起删掉
  assert.deepEqual(vertexDeletionBlockers(geometry, ['0.0.1']), [
    { polygonIndex: 0, vertexCount: 3, removed: 1, remaining: 2 },
  ]);
  assert.deepEqual(vertexDeletionBlockers(geometry, ['2.0.0']), [
    { polygonIndex: 2, vertexCount: 3, removed: 1, remaining: 2 },
  ]);
  // 第 2 个子面是四边形：删 1 个顶点放行
  assert.deepEqual(vertexDeletionBlockers(geometry, ['1.0.3']), []);
  // 各子面分别计数，互不串账
  assert.deepEqual(
    vertexDeletionBlockers(geometry, ['0.0.1', '1.0.0']).map((blocker) => blocker.polygonIndex),
    [0],
  );
  // 多个子面同时命中：按子面序号升序各报一个
  assert.deepEqual(
    vertexDeletionBlockers(geometry, ['2.0.2', '0.0.0']).map((blocker) => blocker.polygonIndex),
    [0, 2],
  );
  // 子面里的洞同样不拦
  const hole = smallHoleRing();
  const withHole = {
    type: 'MultiPolygon',
    coordinates: [[[...convexRing(4), convexRing(4)[0]], [...hole, hole[0]]]],
  };
  assert.deepEqual(vertexDeletionBlockers(withHole, ['0.1.0', '0.1.1', '0.1.2']), []);
});

// 缺陷③回归（必须保留）：旧 polygonVertexCount 对 MultiPolygon 误拦合法删除
test('vertexDeletionBlockers does not mistake a MultiPolygon ring count for a vertex count', () => {
  const legacyPolygonVertexCount = (feature) => {
    // 已删除实现的逐字复刻，仅作回归见证；不得在生产代码里恢复
    const ring = feature?.geometry?.coordinates?.[0];
    if (!Array.isArray(ring) || ring.length === 0) return 0;
    const first = ring[0];
    const last = ring[ring.length - 1];
    const closed = Array.isArray(first) && Array.isArray(last)
      && first[0] === last[0] && first[1] === last[1] && ring.length > 1;
    return closed ? ring.length - 1 : ring.length;
  };
  const geometry = multiPolygonOf(closedPolygon(convexRing(4)), closedPolygon(convexRing(5)));
  // 旧实现取 coordinates[0] 当"外环"，对 MultiPolygon 拿到的是首个子面（环的数组）：
  // 单环子面的"顶点数"= 环数 = 1，命中"0 < 顶点数 <= 3 即拦"
  const legacyCount = legacyPolygonVertexCount({ geometry });
  assert.equal(legacyCount, 1);
  assert.ok(legacyCount > 0 && legacyCount <= 3, '旧实现会把这次合法删除误判为三角面而拦截');
  // 带洞子面（环数 2）同样误拦
  const hole = smallHoleRing();
  const withHole = multiPolygonOf({
    coordinates: [[...convexRing(4), convexRing(4)[0]], [...hole, hole[0]]],
  });
  assert.ok(legacyPolygonVertexCount({ geometry: withHole }) <= 3);

  // 新实现：四边形子面删 1 个顶点 → 放行
  assert.deepEqual(vertexDeletionBlockers(geometry, ['0.0.1']), []);
  assert.deepEqual(vertexDeletionBlockers(geometry, ['1.0.4']), []);
  assert.deepEqual(vertexDeletionBlockers(withHole, ['0.0.2']), []);
});

test('vertexDeletionBlockers parses double-digit indices instead of truncating them', () => {
  // 三角面收到 "0.10"：顶点 10 并不存在，上游 removeCoordinate 的 splice 是空操作，
  // 不得被当成 "0.1" 而误拦
  assert.deepEqual(vertexDeletionBlockers(triangle, ['0.10']), []);
  assert.equal(vertexDeletionBlockers(triangle, ['0.1']).length, 1);
  // 十二边形删 10 个顶点（下标 2..11，含两位数下标）→ 剩 2 个；
  // 若 "0.10"/"0.11" 被截成 "0.1"，去重后只删 9 个，就不会拦
  const twelve = closedPolygon(convexRing(12));
  const tenPaths = Array.from({ length: 10 }, (_, index) => `0.${index + 2}`);
  assert.deepEqual(vertexDeletionBlockers(twelve, tenPaths), [
    { polygonIndex: 0, vertexCount: 12, removed: 10, remaining: 2 },
  ]);
  // "0.10" 与 "0.11" 是两个不同的越界下标，不得被截成合法下标 "0.1" 而凑成 2 个删除
  assert.deepEqual(vertexDeletionBlockers(multiPolygonOf(square), ['0.0.0', '0.0.10', '0.0.11']), []);
  // MultiPolygon 的两位数下标
  const multiTwelve = multiPolygonOf(twelve);
  const doubleDigitPaths = [
    '0.0.10',
    '0.0.11',
    ...Array.from({ length: 8 }, (_, index) => `0.0.${index + 2}`),
  ];
  assert.deepEqual(
    vertexDeletionBlockers(multiTwelve, doubleDigitPaths).map((item) => [item.polygonIndex, item.remaining]),
    [[0, 2]],
  );
});

test('vertexDeletionBlockers returns no blockers for missing geometry, unsupported types, or empty paths', () => {
  for (const geometry of [
    null,
    undefined,
    { type: 'LineString', coordinates: [[0, 0], [1, 1]] },
    { type: 'Polygon', coordinates: 'bad' },
  ]) {
    assert.deepEqual(vertexDeletionBlockers(geometry, ['0.0']), []);
  }
  for (const paths of [[], undefined, null, '0.0', [null, 7, '', 'x.y', '.']]) {
    assert.deepEqual(vertexDeletionBlockers(triangle, paths), []);
  }
  // 环 / 子面缺失：无从比较，不拦（保持上游行为）——等价于旧用例的"空环计 0 个顶点"
  assert.deepEqual(vertexDeletionBlockers({ type: 'Polygon', coordinates: [[]] }, ['0.0']), []);
  assert.deepEqual(vertexDeletionBlockers({ type: 'MultiPolygon', coordinates: [] }, ['0.0.0']), []);
  assert.deepEqual(
    vertexDeletionBlockers({ type: 'MultiPolygon', coordinates: [triangle.coordinates] }, ['5.0.0']),
    [],
  );
  // 单点环删那唯一的点：剩 0 个，拦——等价于旧用例的"单点环计 1 个顶点"
  assert.deepEqual(vertexDeletionBlockers({ type: 'Polygon', coordinates: [[[0, 0]]] }, ['0.0']), [
    { polygonIndex: 0, vertexCount: 1, removed: 1, remaining: 0 },
  ]);
});

test('describeVertexDeletionBlockers names the sub-polygon only for MultiPolygon', () => {
  const tail = '，会连带删除整个面；如需删除整个面，请按 Esc 退出顶点编辑后单击选中面再删除';
  const blocker = { polygonIndex: 1, vertexCount: 4, removed: 2, remaining: 2 };
  assert.equal(
    describeVertexDeletionBlockers([blocker], triangle),
    `本次删除后该面只剩 2 个顶点（最少 3 个）${tail}`,
  );
  const multi = { type: 'MultiPolygon', coordinates: [] };
  assert.equal(
    describeVertexDeletionBlockers([blocker], multi),
    `本次删除后第 2 个子面只剩 2 个顶点（最少 3 个）${tail}`,
  );
  // 多个子面同时命中：逐个说明
  const first = { polygonIndex: 0, vertexCount: 3, removed: 1, remaining: 2 };
  const third = { polygonIndex: 2, vertexCount: 5, removed: 3, remaining: 2 };
  assert.equal(
    describeVertexDeletionBlockers([first, third], multi),
    `本次删除后第 1 个子面只剩 2 个顶点（最少 3 个）；本次删除后第 3 个子面只剩 2 个顶点（最少 3 个）${tail}`,
  );
});

// 工厂测试的夹具：假 base 模式（spy）+ 假模式处理器（spy fireActionable）
function createGuardHarness(geometry, selectedCoordPaths) {
  const calls = { upstream: [], actionable: [], blocked: [], geoJsonReads: 0 };
  const state = {
    featureId: 'f1',
    selectedCoordPaths,
    feature: {
      toGeoJSON: () => {
        calls.geoJsonReads += 1;
        return { type: 'Feature', properties: {}, geometry };
      },
    },
  };
  const base = {
    onSetup: () => 'setup',
    onTrash(modeState) {
      calls.upstream.push({ self: this, state: modeState });
      return 'upstream-result';
    },
  };
  const handler = {
    fireActionable(modeState) {
      calls.actionable.push(modeState);
    },
  };
  return { calls, state, base, handler };
}

test('createGuardedDirectSelect blocks without touching upstream when the deletion would collapse a face', () => {
  const harness = createGuardHarness(triangle, ['0.1']);
  const mode = createGuardedDirectSelect(harness.base, (blockers, geometry) => {
    harness.calls.blocked.push({ blockers, geometry });
  });
  assert.equal(mode.onTrash.call(harness.handler, harness.state), undefined);
  assert.equal(harness.calls.upstream.length, 0, '拦截时不得调用上游 onTrash');
  assert.equal(harness.calls.blocked.length, 1);
  assert.equal(harness.calls.blocked[0].blockers[0].remaining, 2);
  assert.equal(harness.calls.blocked[0].geometry, triangle, '回调拿到 state.feature 的 GeoJSON 几何');
  assert.deepEqual(harness.calls.actionable, [harness.state], '拦截后只同步按钮可用态');
  // 选中路径保持原样（上游会原地排序并清空），用户可以改选别的顶点
  assert.deepEqual(harness.state.selectedCoordPaths, ['0.1']);

  // 未提供回调时同样拦截，且不抛错
  const bare = createGuardHarness(square, ['0.0', '0.2']);
  const bareMode = createGuardedDirectSelect(bare.base);
  assert.doesNotThrow(() => bareMode.onTrash.call(bare.handler, bare.state));
  assert.equal(bare.calls.upstream.length, 0);
  assert.deepEqual(bare.calls.actionable, [bare.state]);
});

test('createGuardedDirectSelect delegates with the same handler and state when the deletion is safe', () => {
  const harness = createGuardHarness(square, ['0.1']);
  const mode = createGuardedDirectSelect(harness.base, () => assert.fail('放行时不应触发拦截回调'));
  assert.equal(mode.onTrash.call(harness.handler, harness.state), 'upstream-result', '上游返回值原样透出');
  assert.equal(harness.calls.upstream.length, 1);
  assert.equal(harness.calls.upstream[0].self, harness.handler, '上游以同一模式处理器为 this 调用');
  assert.equal(harness.calls.upstream[0].state, harness.state, '传入同一份 state');
  assert.equal(harness.calls.actionable.length, 0, '可用态由上游 onTrash 自己同步');
});

test('createGuardedDirectSelect short-circuits an empty selection instead of firing an empty upstream update', () => {
  for (const paths of [[], undefined]) {
    const harness = createGuardHarness(square, paths);
    const mode = createGuardedDirectSelect(harness.base, () => assert.fail('无选中不触发拦截回调'));
    mode.onTrash.call(harness.handler, harness.state);
    assert.equal(harness.calls.upstream.length, 0, '无选中不得调用上游 onTrash');
    assert.deepEqual(harness.calls.actionable, [harness.state]);
    assert.equal(harness.calls.geoJsonReads, 0, '无选中时不需要读几何');
  }
});

test('createGuardedDirectSelect keeps every other upstream method and never mutates the base mode', () => {
  const base = { ...MapboxDraw.modes.direct_select };
  const before = { ...base };
  const mode = createGuardedDirectSelect(base, () => {});
  // onTrash（顶点删除防护）、onMouseDown / onClick / onTap（命中规则修正）、onMidpoint（选中坐标同步）
  const takenOver = new Set(['onTrash', 'onMouseDown', 'onClick', 'onTap', 'onMidpoint']);
  for (const key of Object.keys(before)) {
    if (takenOver.has(key)) {
      assert.notEqual(mode[key], before[key], `${key} 必须被接管`);
    } else {
      assert.equal(mode[key], before[key], `${key} 应原样保留`);
    }
  }
  assert.deepEqual(Object.keys(mode), Object.keys(before));
  assert.deepEqual(base, before, '不得改写传入的基础模式');
});

/**
 * mapbox-gl-draw 1.5.1 面模型的最小复刻（src/feature_types/polygon.js、
 * multi_feature.js），用来驱动真实的 DirectSelect.onTrash：
 * - 内部环不含闭合重复点；
 * - removeCoordinate 删一个顶点，某环剩 <3 点时整个环被删；
 * - isValid = 至少一个环且每个环 >2 点（MultiPolygon 要求每个子面都满足）。
 */
function createDrawPolygonModel(geometry, id = 'f1') {
  const isMulti = geometry.type === 'MultiPolygon';
  const polygons = (isMulti ? geometry.coordinates : [geometry.coordinates])
    .map((rings) => rings.map((ring) => ring.slice(0, -1)));
  const dropCollapsed = (rings) => {
    for (let index = rings.length - 1; index >= 0; index -= 1) {
      if (rings[index].length < 3) rings.splice(index, 1);
    }
  };
  return {
    id,
    removeCoordinate(path) {
      const parts = path.split('.');
      const rings = isMulti ? polygons[Number(parts[0])] : polygons[0];
      const vertexIndex = Number(isMulti ? parts[2] : parts[1]);
      const ringIndex = isMulti ? Number(parts[1]) : Number(parts[0]);
      rings[ringIndex].splice(vertexIndex, 1);
      dropCollapsed(rings);
    },
    isValid() {
      const validRings = (rings) => rings.length > 0 && rings.every((ring) => ring.length > 2);
      return isMulti ? polygons.every(validRings) : validRings(polygons[0]);
    },
    toGeoJSON: () => ({
      id,
      type: 'Feature',
      properties: {},
      geometry: {
        type: geometry.type,
        coordinates: isMulti
          ? polygons.map((rings) => rings.map((ring) => [...ring, ring[0]]))
          : polygons[0].map((ring) => [...ring, ring[0]]),
      },
    }),
  };
}

test('guarded direct_select stops the whole-face deletion that the real upstream onTrash performs', () => {
  // 行为级回归：用 @mapbox/mapbox-gl-draw 1.5.1 的真实 DirectSelect.onTrash 驱动，
  // 断言删除按钮 / 键盘 Delete 汇到的这个入口在防护前后的差别
  const upstream = MapboxDraw.modes.direct_select;
  const runTrash = (mode, geometry, selectedCoordPaths) => {
    const calls = { deleted: [], modeChanges: [], events: [] };
    const state = {
      featureId: 'f1',
      feature: createDrawPolygonModel(geometry, 'f1'),
      selectedCoordPaths: [...selectedCoordPaths],
    };
    // 与上游 object_to_mode 的装配一致：模式对象的方法（fireUpdate / fireActionable）
    // 被复制到模式处理器上，处理器再提供 store / events 能力
    const handler = Object.assign({
      fire: (name, payload) => calls.events.push({ name, action: payload?.action }),
      getSelected: () => [state.feature],
      setActionableState() {},
      clearSelectedCoordinates() {},
      deleteFeature: (ids) => calls.deleted.push(ids),
      changeMode: (name, options) => calls.modeChanges.push({ name, options }),
    }, mode);
    mode.onTrash.call(handler, state);
    return { calls, geometry: state.feature.toGeoJSON().geometry };
  };
  const updateEvent = {
    name: MapboxDraw.constants.events.UPDATE,
    action: MapboxDraw.constants.updateActions.CHANGE_COORDINATES,
  };

  // 未防护的上游：三角面删 1 个顶点 → 整面被删（缺陷根因）
  const unprotected = runTrash(upstream, triangle, ['0.1']);
  assert.deepEqual(unprotected.calls.deleted, [['f1']], '上游确实会删除整个面');
  assert.deepEqual(unprotected.calls.modeChanges, [{ name: 'simple_select', options: {} }]);

  const blocked = [];
  const guarded = createGuardedDirectSelect(upstream, (blockers) => blocked.push(blockers));

  // 防护后：三角面不删、几何不变、不发 draw.update
  const protectedTriangle = runTrash(guarded, triangle, ['0.1']);
  assert.deepEqual(protectedTriangle.calls.deleted, []);
  assert.deepEqual(protectedTriangle.geometry, triangle);
  assert.deepEqual(protectedTriangle.calls.events, []);
  assert.equal(blocked.length, 1);

  // 防护后：四边形删 1 个顶点正常放行——上游跑完、发出 draw.update、几何少 1 点、不删面
  const allowed = runTrash(guarded, square, ['0.1']);
  assert.deepEqual(allowed.calls.deleted, []);
  assert.equal(allowed.geometry.coordinates[0].length, 4, '闭合环 4 个坐标 = 3 个顶点');
  assert.deepEqual(allowed.calls.events, [updateEvent]);

  // 防护后：四边形 Shift 选两个 → 拦
  const multiSelection = runTrash(guarded, square, ['0.0', '0.2']);
  assert.deepEqual(multiSelection.calls.deleted, []);
  assert.deepEqual(multiSelection.geometry, square);
  assert.deepEqual(multiSelection.calls.events, []);

  // MultiPolygon 的三角子面：上游会删掉整个 MultiPolygon（连同四边形子面），防护后不删
  const multi = multiPolygonOf(triangle, square);
  assert.deepEqual(runTrash(upstream, multi, ['0.0.1']).calls.deleted, [['f1']]);
  const protectedMulti = runTrash(guarded, multi, ['0.0.1']);
  assert.deepEqual(protectedMulti.calls.deleted, []);
  assert.deepEqual(protectedMulti.geometry, multi);
});

// ─── 就近命中（2026-11-24）──────────────────────────────────────────────────
// 库的 featuresAt 按 properties.id 去重，而顶点与中点要素都没有 id：同一像素只留
// "渲染在最上面的那一个"（中点图层在顶点图层之上）。onMidpoint 又是在鼠标按下那刻
// 就加顶点，于是相邻顶点靠得近时，点顶点会多出一个顶点；几个顶点圆重叠时选中的
// 是绘制顺序靠后的那个。这里断言择近规则本身，以及它被接到了真实的 onMouseDown 上。

test('pickNearestPointTarget takes the nearest target in its own radius, vertex first on ties', () => {
  const radius = (kind) => (kind === 'vertex' ? 9 : 5);
  const at = (x, y = 100) => ({ x, y });
  const clearance = 7;

  // 点在顶点上，4px 外是中点：顶点（库的原规则会取"最上层"的中点）
  assert.equal(pickNearestPointTarget(
    [{ kind: 'midpoint', ...at(104) }, { kind: 'vertex', ...at(100) }], at(100), radius, { midpointClearance: clearance },
  ).kind, 'vertex');

  // 三个顶点圆重叠：取离点击最近的，而不是列表里（渲染顺序）靠前的
  assert.equal(pickNearestPointTarget(
    [{ kind: 'vertex', ...at(106) }, { kind: 'vertex', ...at(100) }, { kind: 'vertex', ...at(94) }], at(101), radius,
  ).x, 100);

  // 两侧顶点各在 12px 外：点在中间的小圆点上，新增顶点
  assert.equal(pickNearestPointTarget(
    [{ kind: 'vertex', ...at(112) }, { kind: 'vertex', ...at(88) }, { kind: 'midpoint', ...at(100) }], at(100), radius, { midpointClearance: clearance },
  ).kind, 'midpoint');

  // 两侧顶点各在 5px 外：小圆点的中心被顶点圆盖住，不参与——怎么点都不会多出顶点；
  // 两个顶点距离并列，取到的是顶点
  const covered = pickNearestPointTarget(
    [{ kind: 'vertex', ...at(105) }, { kind: 'vertex', ...at(95) }, { kind: 'midpoint', ...at(100) }], at(100), radius, { midpointClearance: clearance },
  );
  assert.equal(covered.kind, 'vertex');
  assert.equal(covered.x, 105);

  // 不传 clearance 时中点照常参与（纯择近）
  assert.equal(pickNearestPointTarget(
    [{ kind: 'vertex', ...at(105) }, { kind: 'vertex', ...at(95) }, { kind: 'midpoint', ...at(100) }], at(100), radius,
  ).kind, 'midpoint');

  // 距离只差亚像素：顶点优先
  assert.equal(pickNearestPointTarget(
    [{ kind: 'midpoint', ...at(104) }, { kind: 'vertex', ...at(104.3) }], at(100), radius,
  ).kind, 'vertex');

  // 超出各自命中半径的不参与；都不在半径内、或没有顶点/中点类目标时返回 null
  assert.equal(pickNearestPointTarget([{ kind: 'midpoint', ...at(105.5) }], at(100), radius), null);
  assert.equal(pickNearestPointTarget([{ kind: 'vertex', ...at(109.5) }], at(100), radius), null);
  assert.equal(pickNearestPointTarget([{ kind: 'feature', ...at(100) }], at(100), radius), null);
  assert.equal(pickNearestPointTarget([], at(100), radius), null);
  assert.equal(pickNearestPointTarget(undefined, at(100), radius), null);
});

test('createGuardedDirectSelect hands the pressed button the nearest vertex instead of the topmost midpoint', () => {
  const upstream = MapboxDraw.modes.direct_select;
  const pointFeature = (meta, lng, extra = {}) => ({
    properties: { meta, ...extra },
    geometry: { type: 'Point', coordinates: [lng, 0] },
  });
  // 两个顶点相距 gapPx，中点在正中；map.project 把经度换成横坐标（像素）
  const build = (gapPx) => {
    const vertexA = pointFeature('vertex', 1, { coord_path: '0.3' });
    const vertexB = pointFeature('vertex', 2, { coord_path: '0.4' });
    const midpoint = pointFeature('midpoint', 1.5, { coord_path: '0.4', lng: 1.5, lat: 0 });
    const pixels = new Map([[1, { x: 100, y: 100 }], [2, { x: 100 + gapPx, y: 100 }], [1.5, { x: 100 + gapPx / 2, y: 100 }]]);
    const queries = [];
    const map = {
      // 渲染顺序（库查询结果的顺序）：中点图层在最上
      queryRenderedFeatures(box, options) {
        queries.push({ box, layers: [...options.layers].sort() });   // 只记录缓冲盒查询（点查询在面重叠时才有）
        return [midpoint, vertexB, vertexA];
      },
      project: (coordinates) => pixels.get(coordinates[0]),
    };
    return { vertexA, vertexB, midpoint, queries, map };
  };
  const run = (gapPx, x) => {
    const { queries, map } = build(gapPx);
    const seen = [];
    const base = {
      ...upstream,
      onVertex(state, event) {
        seen.push(`vertex ${event.featureTarget.properties.coord_path}`);
        return 'vertex-result';
      },
      onMidpoint(state, event) {
        seen.push(`midpoint ${event.featureTarget.properties.coord_path}`);
        return 'midpoint-result';
      },
    };
    // 与上游 object_to_mode 的装配一致：模式对象的方法被复制到模式处理器上
    const handler = Object.assign({
      map,
      drawConfig: {
        styles: [{ id: 'gl-draw-polygon-fill.cold' }, { id: 'gl-draw-vertex-outer.hot' }, { id: 'gl-draw-midpoint.cold' }],
        clickBuffer: 2,
      },
    }, base);
    const mode = createGuardedDirectSelect(base, () => assert.fail('按下不该触发删除拦截'));
    const event = { point: { x, y: 100 }, featureTarget: build(gapPx).midpoint };
    const result = mode.onMouseDown.call(handler, {}, event);
    return { result, seen, queries };
  };

  // 点在顶点上（间距 8px，中点就在 4px 外的点击缓冲范围里）：选中顶点，不会多出顶点
  const onVertex = run(8, 100);
  assert.deepEqual(onVertex.result, 'vertex-result');
  assert.deepEqual(onVertex.seen, ['vertex 0.3']);
  // 同一个点击缓冲盒、同一组绘图图层；附近没有面重叠时不再做第二次查询
  assert.deepEqual(onVertex.queries, [{ box: [[98, 98], [102, 102]], layers: ['gl-draw-midpoint.cold', 'gl-draw-polygon-fill.cold', 'gl-draw-vertex-outer.hot'] }], '与库用同一个点击缓冲盒和同一组绘图图层');

  // 间距 8px 时点在中间的小圆点上：小圆点被顶点圆盖住，仍然选中顶点（两侧顶点距离并列，
  // 取到哪一个都行——关键是不会新增顶点）
  const coveredMidpoint = run(8, 104);
  assert.equal(coveredMidpoint.result, 'vertex-result');
  assert.match(coveredMidpoint.seen[0], /^vertex 0\.[34]$/);

  // 间距 24px 时点在中间的小圆点上：新增顶点
  assert.deepEqual(run(24, 112).seen, ['midpoint 0.4']);

  // 附近没有点目标、而点击处叠着两个面：目标换成应当选中的那个面，再交给上游
  const onSmallerPolygon = (() => {
    const polygon = (id, x, size, active) => ({
      properties: { meta: 'feature', id, active },
      geometry: { type: 'Polygon', coordinates: [[[x, 0], [x + size, 0], [x + size, size], [x, size], [x, 0]]] },
    });
    const big = polygon('big', 0, 100, 'true');
    const small = polygon('small', 40, 20, 'false');
    const seen = [];
    const base = {
      ...upstream,
      onFeature(state, event) {
        seen.push(`feature ${event.featureTarget.properties.id}`);
        return 'feature-result';
      },
    };
    const handler = Object.assign({
      map: {
        queryRenderedFeatures: (geometry) => (Array.isArray(geometry[0]) ? [big, small] : [small]),
        project: ([lng, lat]) => ({ x: lng, y: lat }),
      },
      drawConfig: { styles: [{ id: 'gl-draw-polygon-fill.cold' }], clickBuffer: 2 },
    }, base);
    const event = { point: { x: 50, y: 10 }, featureTarget: big };
    const result = createGuardedDirectSelect(base, () => {}).onMouseDown.call(handler, {}, event);
    return { result, seen, target: event.featureTarget.properties.id };
  })();
  // 库选中的是当前面（大面，处于顶点模式）；点击点其实落在更小的面里 → 换成小面，
  // 上游据此不再启动拖动（小面不是当前面），抬起时退回普通选择
  assert.deepEqual(onSmallerPolygon, { result: undefined, seen: [], target: 'small' });

  // 库自身的命中已经是对的（附近没有更近的点）时，原样交给上游
  const passthrough = (() => {
    const seen = [];
    const base = {
      ...upstream,
      onFeature() {
        seen.push('feature');
        return 'feature-result';
      },
    };
    const handler = Object.assign({ map: { queryRenderedFeatures: () => [] }, drawConfig: { styles: [{ id: 'gl-draw-vertex-outer.hot' }], clickBuffer: 2 } }, base);
    const event = { point: { x: 100, y: 100 }, featureTarget: { properties: { meta: 'feature', active: 'true' } } };
    const result = createGuardedDirectSelect(base, () => {}).onMouseDown.call(handler, {}, event);
    return { result, seen, target: event.featureTarget };
  })();
  assert.deepEqual(passthrough, {
    result: 'feature-result',
    seen: ['feature'],
    target: { properties: { meta: 'feature', active: 'true' } },
  });

  // 查询抛错、或样式里没有点图层：退回库的命中，不让一次点击失效
  for (const handler of [
    { map: { queryRenderedFeatures() { throw new Error('style not loaded'); } }, drawConfig: { styles: [{ id: 'gl-draw-midpoint.cold' }], clickBuffer: 2 } },
    { map: { queryRenderedFeatures: () => assert.fail('没有点图层时不该查询') }, drawConfig: { styles: [{ id: 'gl-draw-polygon-fill.cold' }], clickBuffer: 2 } },
    { map: {}, drawConfig: {} },
  ]) {
    const base = { ...upstream, onMidpoint: () => 'midpoint-result' };
    const event = { point: { x: 100, y: 100 }, featureTarget: { properties: { meta: 'midpoint' } } };
    assert.equal(createGuardedDirectSelect(base, () => {}).onMouseDown.call(Object.assign({}, handler, base), {}, event), 'midpoint-result');
  }
});

test('createStepBackDrawPolygon backs up one placed vertex and only discards an empty sketch', () => {
  const upstream = MapboxDraw.modes.draw_polygon;
  const mode = createStepBackDrawPolygon(upstream);

  // mapbox-gl-draw 1.5.1 绘制草稿的最小复刻（src/feature_types/polygon.js）：内部环不含闭合点
  const changes = [];
  const sketch = {
    id: 'sketch-1',
    coordinates: [[]],
    changed() {
      changes.push(JSON.stringify(this.coordinates));
    },
    updateCoordinate(path, lng, lat) {
      const [ringIndex, coordIndex] = path.split('.').map(Number);
      if (this.coordinates[ringIndex] === undefined) this.coordinates[ringIndex] = [];
      this.coordinates[ringIndex][coordIndex] = [lng, lat];
      this.changed();
    },
  };
  const deleted = [];
  const modeChanges = [];
  const handler = {
    updateUIClasses() {},
    deleteFeature: (ids) => deleted.push(ids),
    changeMode: (name, options) => modeChanges.push({ name, options }),
  };
  const state = { polygon: sketch, currentVertexPosition: 0 };
  // 用上游真实的 clickAnywhere / onMouseMove 推进草稿：第 k 次点击后已放置 k 个点，
  // 外环是 [p0 … p(k-1), 跟随鼠标的点]
  const click = (x, y) => upstream.clickAnywhere.call(handler, state, { lngLat: { lng: x, lat: y } });
  const move = (x, y) => upstream.onMouseMove.call(handler, state, { lngLat: { lng: x, lat: y } });
  const ring = () => sketch.coordinates[0].map(([x, y]) => [x, y]);

  click(0, 0);
  click(10, 0);
  click(10, 10);
  click(0, 10);
  move(4, 4);
  assert.equal(state.currentVertexPosition, 4);
  assert.deepEqual(ring(), [[0, 0], [10, 0], [10, 10], [0, 10], [4, 4]]);

  // 退回一个点：最后一个已放置的点被摘掉，跟随鼠标的点顺位补上
  const changesBefore = changes.length;
  mode.onTrash.call(handler, state);
  assert.equal(state.currentVertexPosition, 3);
  assert.deepEqual(ring(), [[0, 0], [10, 0], [10, 10], [4, 4]]);
  assert.equal(changes.length, changesBefore + 1, '草稿被标记为已变更，库才会重绘');
  assert.deepEqual(deleted, [], '有点可退时不得丢弃草稿');
  assert.deepEqual(modeChanges, []);

  // 状态自洽：鼠标移动写的仍是"跟随点"那一格，再点一次就接回原来的形状
  move(6, 6);
  assert.deepEqual(ring(), [[0, 0], [10, 0], [10, 10], [6, 6]]);
  click(0, 10);
  assert.equal(state.currentVertexPosition, 4);
  assert.deepEqual(ring(), [[0, 0], [10, 0], [10, 10], [0, 10], [0, 10]]);

  // 退到只剩一个点：不会像 polygon.removeCoordinate 那样把不足 3 个点的环整个删掉，
  // 而是停在"还没放置任何点"的初始形态
  for (let step = 0; step < 4; step += 1) mode.onTrash.call(handler, state);
  assert.equal(state.currentVertexPosition, 0);
  assert.deepEqual(ring(), [[0, 10]], '跟随鼠标的那个点留在第 0 格，下一次移动/点击会覆盖它');
  assert.deepEqual(deleted, []);
  move(3, 3);
  click(5, 5);
  assert.equal(state.currentVertexPosition, 1);
  assert.deepEqual(ring(), [[5, 5], [5, 5]], '重新开始绘制后与第一次点击的形态一致');
  mode.onTrash.call(handler, state);
  assert.equal(state.currentVertexPosition, 0);
  assert.deepEqual(ring(), [[5, 5]]);

  // 一个点都没放时沿用上游：丢弃草稿，回到选择模式
  mode.onTrash.call(handler, state);
  assert.equal(state.currentVertexPosition, 0);
  assert.deepEqual(deleted, [['sketch-1']]);
  assert.deepEqual(modeChanges, [{ name: 'simple_select', options: undefined }]);
  assert.equal(mode.onSetup, upstream.onSetup);
  assert.equal(mode.onStop, upstream.onStop);
});

test('createGuardedSimpleSelect picks the polygon that should be selected, not the first one in tile order', () => {
  // 库的"面按面积从小到大命中"从未生效：sort_features 给面积函数传的是 Feature，
  // @mapbox/geojson-area 0.2.2 对它返回 undefined，比较结果恒为 NaN。于是命中的永远是
  // 瓦片里排在前面的那个面——新画的小面叠在大面上，点小面选中的是大面。
  const upstream = MapboxDraw.modes.simple_select;
  const polygon = (id, x, size) => ({
    properties: { meta: 'feature', id, active: 'false' },
    geometry: { type: 'Polygon', coordinates: [[[x, 0], [x + size, 0], [x + size, size], [x, size], [x, 0]]] },
  });
  const big = polygon('big', 0, 100);
  const bigLineCopy = { ...big, layer: 'gl-draw-lines.cold' };            // 同一个面也出现在描边层
  const smallInside = polygon('small', 40, 20);                           // 完全落在大面里
  const smallBeside = polygon('beside', 101, 20);                         // 贴着大面的右边
  const vertex = { properties: { meta: 'vertex', parent: 'big', coord_path: '0.1' }, geometry: { type: 'Point', coordinates: [100, 0] } };

  const run = ({ point, boxHits, pointHits, target, throwOnQuery = false, fillOnly = true }) => {
    const queries = [];
    const map = {
      queryRenderedFeatures(geometry, options) {
        if (throwOnQuery) throw new Error('style not loaded');
        queries.push({ box: Array.isArray(geometry[0]), layers: options.layers });
        // 只认填充图层：描边图层的命中带容差，不能拿来判断"点击点在哪个面里"
        if (!Array.isArray(geometry[0])) return fillOnly && options.layers.some((id) => !id.includes('polygon-fill')) ? [] : pointHits;
        return boxHits;
      },
      // 经度、纬度与像素 1:1：面积就是边长平方
      project: ([lng, lat]) => ({ x: lng, y: lat }),
    };
    const seen = [];
    // 顶点 / 中点要素没有 id，只有 meta：统一记 id，没有就记 meta
    const targetName = (event) => event.featureTarget.properties.id || event.featureTarget.properties.meta;
    const base = {
      ...upstream,
      onMouseDown(state, event) {
        seen.push(`down ${targetName(event)}`);
        return 'down';
      },
      onClick(state, event) {
        seen.push(`click ${targetName(event)}`);
        return 'click';
      },
    };
    base.onTap = base.onClick;   // 上游 SimpleSelect.onTap 与 onClick 是同一个函数
    const handler = Object.assign({
      map,
      // 与编辑器 drawStyles() 经库加 .cold / .hot 后缀后的形态一致：带图层类型
      drawConfig: { styles: [{ id: 'gl-draw-polygon-fill.cold', type: 'fill' }, { id: 'gl-draw-lines.cold', type: 'line' }], clickBuffer: 2 },
    }, base);
    const mode = createGuardedSimpleSelect(base);
    const event = { point, featureTarget: target };
    const results = [
      mode.onMouseDown.call(handler, {}, event),
      mode.onClick.call(handler, {}, event),
      mode.onTap.call(handler, {}, event),
    ];
    const pointQueries = queries.filter((query) => !query.box);
    // 判断"点击点在哪个面里"的查询只带填充图层
    if (pointQueries.length) assert.deepEqual(pointQueries[0].layers, ['gl-draw-polygon-fill.cold'], '包含判定只能查填充图层');
    return { results, seen, boxQueries: queries.filter((query) => query.box).length, pointQueries: pointQueries.length };
  };

  // 点击点同时落在两个面里（小面完全叠在大面里）：取面积最小的
  assert.deepEqual(
    run({ point: { x: 50, y: 10 }, boxHits: [big, bigLineCopy, smallInside], pointHits: [big, smallInside], target: big }),
    { results: ['down', 'click', 'click'], seen: ['down small', 'click small', 'click small'], boxQueries: 3, pointQueries: 3 },
    '重叠时选最小的面；同一个面的填充层与描边层副本不重复计入',
  );
  // 点击点只落在大面里，小面只是被 2px 点击缓冲擦到：选大面（贴边的小图斑不抢点击）
  assert.deepEqual(
    run({ point: { x: 100.5, y: 10 }, boxHits: [big, bigLineCopy, smallBeside], pointHits: [big], target: big }),
    { results: ['down', 'click', 'click'], seen: ['down big', 'click big', 'click big'], boxQueries: 3, pointQueries: 3 },
    '点击点只在一个面里就选它',
  );
  // 只命中一个面：库选的就是它，不做第二次查询
  assert.deepEqual(
    run({ point: { x: 20, y: 50 }, boxHits: [big, bigLineCopy], pointHits: [big], target: big }),
    { results: ['down', 'click', 'click'], seen: ['down big', 'click big', 'click big'], boxQueries: 3, pointQueries: 0 },
  );
  // 库已经选中点目标（已选中面的顶点标记）：点优先，不查询
  assert.deepEqual(
    run({ point: { x: 100, y: 0 }, boxHits: [vertex, big, smallInside], pointHits: [big, smallInside], target: vertex }),
    { results: ['down', 'click', 'click'], seen: ['down vertex', 'click vertex', 'click vertex'], boxQueries: 0, pointQueries: 0 },
    '点目标（顶点）优先于面',
  );
  // 查询抛错：退回库的命中结果，不让点击失效
  assert.deepEqual(
    run({ point: { x: 50, y: 10 }, boxHits: [big, smallInside], pointHits: [], target: big, throwOnQuery: true }),
    { results: ['down', 'click', 'click'], seen: ['down big', 'click big', 'click big'], boxQueries: 0, pointQueries: 0 },
  );

  const mode = createGuardedSimpleSelect({ ...upstream });
  const before = { ...upstream };
  assert.equal(mode.onSetup, before.onSetup);
  assert.equal(mode.onMouseUp, before.onMouseUp);
  assert.equal(mode.clickOnFeature, before.clickOnFeature);
});

test('createGuardedDirectSelect syncs the selected coordinates after a midpoint adds a vertex', () => {
  // 上游 onMidpoint 加完顶点只改 state.selectedCoordPaths，库里的选中坐标（getSelectedPoints）
  // 仍是空的：新顶点显示为选中，此刻按 Delete 却先弹"请先点击选中某个顶点"，随后又把它删掉
  const upstream = MapboxDraw.modes.direct_select;
  const run = (base, guarded) => {
    const coordinates = [[[0, 0], [10, 0], [10, 10], [0, 10]]];      // 内部环不含闭合点（库的面模型）
    const feature = {
      id: 'f1',
      addCoordinate(path, lng, lat) {
        coordinates[0].splice(Number(path.split('.')[1]), 0, [lng, lat]);
      },
      toGeoJSON: () => ({ id: 'f1', type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [coordinates[0].concat([coordinates[0][0]])] } }),
    };
    const state = { featureId: 'f1', feature, selectedCoordPaths: [], canDragMove: false, initialDragPanState: null, dragMoveLocation: null };
    const synced = [];
    const events = [];
    const handler = Object.assign({
      map: { dragPan: { isEnabled: () => true, disable() {}, enable() {} } },
      fire: (name) => events.push(name),
      getSelected: () => [feature],
      setSelectedCoordinates(coords) {
        synced.push(coords.map((coordinate) => coordinate.coord_path));
      },
    }, base);
    const event = { lngLat: { lng: 5, lat: 0 }, // Polygon 的坐标路径形如 '环.顶点'
      featureTarget: { properties: { meta: 'midpoint', coord_path: '0.1', lng: 5, lat: 0 } } };
    const mode = guarded ? createGuardedDirectSelect(base, () => assert.fail('加顶点不该触发删除拦截')) : base;
    mode.onMidpoint.call(handler, state, event);
    return { ring: coordinates[0], selectedPaths: state.selectedCoordPaths, synced, events };
  };

  const unguarded = run({ ...upstream }, false);
  assert.deepEqual(unguarded.ring, [[0, 0], [5, 0], [10, 0], [10, 10], [0, 10]], '上游确实在 "1" 号位置插入了顶点');
  assert.deepEqual(unguarded.selectedPaths, ['0.1']);
  assert.deepEqual(unguarded.synced, [], '上游不同步库里的选中坐标——这就是缺陷');

  const guarded = run({ ...upstream }, true);
  assert.deepEqual(guarded.ring, unguarded.ring, '几何行为与上游一致');
  assert.deepEqual(guarded.events, unguarded.events, '事件与上游一致');
  assert.deepEqual(guarded.synced, [['0.1']], '加顶点后把新顶点同步为库里的选中坐标');
});
