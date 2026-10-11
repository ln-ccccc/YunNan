import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';

import { buildEditableFeatureCollection, isEditableVectorStatus } from '../src/utils/classificationEditor.mjs';
import { createEditorHistory, serializeEditorSnapshot } from '../src/utils/classificationEditorHistory.mjs';

const require = createRequire(import.meta.url);
const { parse } = require('@vue/compiler-sfc');
const { descriptor } = parse(fs.readFileSync(
  new URL('../src/views/mainfun/ClassificationResultEditor.vue', import.meta.url), 'utf8',
));
const script = descriptor.script.content.replace(/^import[\s\S]*?;\s*/gm, '').replace('export default', 'return');
const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const collection = (classCode = 0) => ({
  type: 'FeatureCollection',
  features: [{
    type: 'Feature', id: 'draw-1', properties: { feature_id: 'auto-10-0-0001', class_code: classCode },
    geometry: { type: 'Polygon', coordinates: [[[100, 25], [101, 25], [101, 26], [100, 25]]] },
  }],
});

function editor(overrides = {}) {
  const messages = [];
  let drawing = collection();
  const dependencies = {
    buildEditableFeatureCollection, createEditorHistory, serializeEditorSnapshot, isEditableVectorStatus,
    window: { location: { search: '?project_id=1&result_id=10' }, confirm: () => true },
    readClassificationResultContext: () => ({ projectId: 1, resultId: 10 }),
    getClassificationResult: async () => ({ vector_status: 'ready', current_revision_no: 0, current_feature_collection: collection() }),
    getClassificationRevisions: async () => ({ revisions: [] }),
    resolveSecureTileTemplate: () => null, global: { BASEURL: 'http://localhost:5008' },
    ...overrides,
  };
  const options = new Function('dependencies', `with (dependencies) { ${script} }`)(dependencies);
  const instance = {
    ...options.data(), ...options.methods,
    loading: false,
    context: { projectId: 1, resultId: 10 },
    result: { vector_status: 'ready', current_revision_no: 0, current_feature_collection: collection() },
    draw: {
      getAll: () => structuredClone(drawing),
      set: (value) => { drawing = structuredClone(value); },
      setFeatureProperty: (id, key, value) => { drawing.features.find((feature) => feature.id === id).properties[key] = value; },
    },
    selectedDrawIds: ['draw-1'],
    $message: { success: (text) => messages.push(text), error: (text) => messages.push(text) },
    $nextTick: async () => {}, initializeMap() {},
  };
  instance.resetEditHistory();
  instance.selectClass(2);
  return { instance, options, messages, drawing: () => drawing };
}

test('saving locks class changes until the accepted revision becomes the new baseline', async () => {
  const pending = deferred();
  const { instance, drawing } = editor({ saveClassificationRevision: () => pending.promise });
  const saving = instance.saveDrawing();
  instance.selectClass(3);
  assert.equal(drawing().features[0].properties.class_code, 2);
  pending.resolve({ current_revision_no: 1, feature_collection: collection(2) });
  await saving;
  assert.equal(instance.isDirty, false);
  assert.equal(instance.result.current_revision_no, 1);
  instance.selectedDrawIds = ['draw-1'];
  instance.selectClass(3);
  assert.equal(instance.isDirty, true);
});

test('an old save success cannot alter another result or release its pending save', async () => {
  const pending = deferred();
  const { instance, messages, drawing } = editor({ saveClassificationRevision: () => pending.promise });
  const saving = instance.saveDrawing();
  instance.context = { projectId: 2, resultId: 20 };
  instance.result = { current_revision_no: 4, current_feature_collection: collection(4) };
  instance.draw.set(collection(4));
  instance.saving = true;
  pending.resolve({ current_revision_no: 1, feature_collection: collection(2) });
  await saving;
  assert.equal(instance.result.current_revision_no, 4);
  assert.equal(drawing().features[0].properties.class_code, 4);
  assert.equal(instance.saving, true);
  assert.deepEqual(messages, []);
});

test('an old save error cannot show an error in the new result', async () => {
  const pending = deferred();
  const { instance, messages } = editor({ saveClassificationRevision: () => pending.promise });
  const saving = instance.saveDrawing();
  instance.context = { projectId: 2, resultId: 20 };
  pending.reject(Object.assign(new Error('old conflict'), { status: 409 }));
  await saving;
  assert.deepEqual(messages, []);
});

test('a save response after reloading the same result cannot overwrite the reload', async () => {
  const pending = deferred();
  const { instance, drawing } = editor({ saveClassificationRevision: () => pending.promise });
  const saving = instance.saveDrawing();
  instance.reloadSeq = (instance.reloadSeq || 0) + 1;
  instance.result = { current_revision_no: 4, current_feature_collection: collection(4) };
  instance.draw.set(collection(4));
  pending.resolve({ current_revision_no: 1, feature_collection: collection(2) });
  await saving;
  assert.equal(instance.result.current_revision_no, 4);
  assert.equal(drawing().features[0].properties.class_code, 4);
});

test('a conflict preserves the current drawing, dirty state and base revision', async () => {
  const { instance, drawing, messages } = editor({
    saveClassificationRevision: async () => { throw Object.assign(new Error('conflict'), { status: 409 }); },
  });
  await instance.saveDrawing();
  assert.equal(instance.result.current_revision_no, 0);
  assert.equal(drawing().features[0].properties.class_code, 2);
  assert.equal(instance.isDirty, true);
  assert.equal(instance.saving, false);
  assert.match(messages[0], /不会自动覆盖/);
});

test('canceling a dirty reload keeps the map, drawing, context and revision intact', async () => {
  let confirms = 0;
  const { instance, drawing } = editor({ window: { location: { search: '' }, confirm: () => { confirms++; return false; } } });
  const draw = instance.draw;
  await instance.reloadResult();
  assert.equal(confirms, 1);
  assert.equal(instance.draw, draw);
  assert.equal(instance.isDirty, true);
  assert.equal(instance.context.resultId, 10);
  assert.equal(drawing().features[0].properties.class_code, 2);
});

test('initial loading does not prompt; route leave and update protect dirty changes', async () => {
  let confirms = 0;
  const { instance, options } = editor({ window: { location: { search: '' }, confirm: () => { confirms++; return false; } } });
  instance.isDirty = false;
  await instance.reloadResult();
  assert.equal(confirms, 0);
  instance.isDirty = true;
  assert.equal(typeof options.beforeRouteLeave, 'function');
  assert.equal(typeof options.beforeRouteUpdate, 'function');
  assert.equal(options.beforeRouteLeave.call(instance), false);
  assert.equal(options.beforeRouteUpdate.call(instance), false);
  assert.equal(confirms, 2);
});

test('revision history arriving after a context switch is ignored', async () => {
  const pending = deferred();
  const { instance } = editor({ getClassificationRevisions: () => pending.promise });
  const loading = instance.loadRevisions();
  instance.context = { projectId: 2, resultId: 20 };
  instance.revisions = [{ revision_no: 4 }];
  pending.resolve({ revisions: [{ revision_no: 1 }] });
  await loading;
  assert.deepEqual(instance.revisions, [{ revision_no: 4 }]);
});

test('the map is not initialized after unmounting during nextTick', async () => {
  const pending = deferred();
  const { instance } = editor();
  instance.isDirty = false;
  let initialized = 0;
  instance.$nextTick = () => pending.promise;
  instance.initializeMap = () => { initialized++; };
  const loading = instance.reloadResult();
  await Promise.resolve();
  await Promise.resolve();
  instance.reloadSeq = -1;
  instance.destroyed = true;
  pending.resolve();
  await loading;
  assert.equal(initialized, 0);
});

test('a confirmed route update reloads without asking a second time', async () => {
  let confirms = 0;
  const { instance, options } = editor({ window: { location: { search: '' }, confirm: () => { confirms++; return true; } } });
  assert.equal(options.beforeRouteUpdate.call(instance), true);
  await options.watch['$route.fullPath'].call(instance);
  assert.equal(confirms, 1);
  assert.equal(instance.isDirty, false);
});

test('refreshing a dirty editor requests the browser leave confirmation', () => {
  const { instance } = editor();
  let prevented = 0;
  const event = { preventDefault: () => { prevented++; } };
  instance.handleBeforeUnload(event);
  assert.equal(prevented, 1);
  assert.equal(event.returnValue, '');
  instance.isDirty = false;
  instance.handleBeforeUnload(event);
  assert.equal(prevented, 1);
});

test('a save finishing after unmount is ignored and repeated save clicks create one request', async () => {
  const pending = deferred();
  let calls = 0;
  const { instance, messages } = editor({ saveClassificationRevision: () => { calls++; return pending.promise; } });
  const saving = instance.saveDrawing();
  await instance.saveDrawing();
  assert.equal(calls, 1);
  instance.destroyed = true;
  pending.resolve({ current_revision_no: 1, feature_collection: collection(2) });
  await saving;
  assert.equal(instance.result.current_revision_no, 0);
  assert.deepEqual(messages, []);
});

test('manual reload during saving keeps the map and does not ask to discard', async () => {
  const pending = deferred();
  let confirms = 0;
  const { instance } = editor({
    saveClassificationRevision: () => pending.promise,
    window: { location: { search: '' }, confirm: () => { confirms++; return true; } },
  });
  const draw = instance.draw;
  const saving = instance.saveDrawing();
  await instance.reloadResult();
  assert.equal(confirms, 0);
  assert.equal(instance.draw, draw);
  assert.equal(instance.saving, true);
  assert.equal(instance.result.current_revision_no, 0);
  pending.resolve({ current_revision_no: 1, feature_collection: collection(2) });
  await saving;
  assert.equal(instance.result.current_revision_no, 1);
});

test('a confirmed route switch during saving loads the new context and ignores the old save', async () => {
  const pending = deferred();
  let confirms = 0;
  const { instance, options, messages } = editor({
    saveClassificationRevision: () => pending.promise,
    window: { location: { search: '' }, confirm: () => { confirms++; return true; } },
    readClassificationResultContext: () => ({ projectId: 2, resultId: 20 }),
    getClassificationResult: async () => ({ vector_status: 'ready', current_revision_no: 4, current_feature_collection: collection(4) }),
  });
  const saving = instance.saveDrawing();
  assert.equal(options.beforeRouteUpdate.call(instance), true);
  options.watch['$route.fullPath'].call(instance);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(confirms, 1);
  assert.deepEqual(instance.context, { projectId: 2, resultId: 20 });
  assert.equal(instance.result.current_revision_no, 4);
  assert.equal(instance.saving, false);
  pending.resolve({ current_revision_no: 1, feature_collection: collection(2) });
  await saving;
  assert.equal(instance.result.current_revision_no, 4);
  assert.deepEqual(messages, []);
});
