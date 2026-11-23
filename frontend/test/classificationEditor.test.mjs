import test from 'node:test';
import assert from 'node:assert/strict';
import MapboxDraw from '@mapbox/mapbox-gl-draw';

import {
  basemapCoverageNotice,
  buildAssignedFeatureIdMap,
  buildEditableFeatureCollection,
  createGuardedDirectSelect,
  describeVertexDeletionBlockers,
  draftExportFilename,
  isEditableVectorStatus,
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
  for (const key of Object.keys(before)) {
    if (key === 'onTrash') {
      assert.notEqual(mode[key], before[key], 'onTrash 必须被接管');
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
