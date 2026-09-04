import test from 'node:test';
import assert from 'node:assert/strict';

import {
  buildEditableFeatureCollection,
  isEditableVectorStatus,
  resolveSecureTileTemplate,
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
