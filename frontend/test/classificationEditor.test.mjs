import test from 'node:test';
import assert from 'node:assert/strict';

import {
  buildAssignedFeatureIdMap,
  buildEditableFeatureCollection,
  isEditableVectorStatus,
  resolveSecureTileTemplate,
  resolveTileZoomRange,
  rewriteFeatureCollectionJson,
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
