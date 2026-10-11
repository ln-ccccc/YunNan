import test from 'node:test';
import assert from 'node:assert/strict';
import * as context from '../src/utils/interpretationContext.mjs';

test('ROI request carries the selected enhancement and denoise options', () => {
  assert.equal(typeof context.buildRoiInferencePayload, 'function');
  assert.deepEqual(context.buildRoiInferencePayload('/uploads/scene.tif', '2024', 7, { prehandle: 2, denoise: 5 }), {
    old_tif_path: '/uploads/scene.tif', new_tif_path: '/uploads/scene.tif', year: '2024',
    device: 'auto', project_id: 7, prehandle: 2, denoise: 5,
  });
});

test('ROI request defaults to no processing and leaves independent context valid', () => {
  assert.equal(typeof context.buildRoiInferencePayload, 'function');
  const payload = context.buildRoiInferencePayload('/uploads/scene.tif', '2024', null);
  assert.equal(payload.prehandle, 0);
  assert.equal(payload.denoise, 0);
  assert.equal('project_id' in payload, false);
});
