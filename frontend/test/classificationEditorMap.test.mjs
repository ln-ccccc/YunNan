import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';

const require = createRequire(import.meta.url);
const { parse } = require('@vue/compiler-sfc');
const { descriptor } = parse(fs.readFileSync(
  new URL('../src/views/mainfun/ClassificationResultEditor.vue', import.meta.url), 'utf8',
));
const script = descriptor.script.content.replace(/^import[\s\S]*?;\s*/gm, '').replace('export default', 'return');

function setup() {
  const events = new Map();
  const controls = [];
  let config;
  let fits = 0;
  class TestMap {
    constructor(value) { config = value; }
    on(name, handler) { events.set(name, handler); }
    once(name, handler) { events.set(name, handler); }
    addControl(control) { controls.push(control); }
  }
  class TestDraw {}
  TestDraw.constants = { classes: {} };
  const dependencies = {
    Map: TestMap, MapboxDraw: TestDraw, NavigationControl: class {},
    global: { BASEURL: 'http://localhost:5008/' }, classColorExpression: () => '#000000',
  };
  const options = new Function('dependencies', `with (dependencies) { ${script} }`)(dependencies);
  const instance = {
    ...options.data(), ...options.methods,
    $refs: { mapContainer: {} }, tileTemplate: 'http://localhost:5008/tiles/{z}/{x}/{y}.png',
    result: { map_manifest: { bounds: [102.44, 24.77, 102.46, 24.80], min_zoom: 8, max_zoom: 17 } },
    resetDrawing() {}, fitToCurrentFeatures() { fits++; },
  };
  instance.initializeMap();
  return { instance, events, controls, config, fits: () => fits };
}

test('editor restricts offline tile requests to the published bounds and native zoom range', () => {
  const { config } = setup();
  const source = config.style.sources.project_basemap;
  assert.deepEqual(source.bounds, [102.44, 24.77, 102.46, 24.80]);
  assert.equal(source.minzoom, 8);
  assert.equal(source.maxzoom, 17);
});

test('editor fits its data and exposes navigation before waiting for raster tiles', () => {
  const { events, controls, fits } = setup();
  assert.equal(controls.length, 1);
  assert.equal(fits(), 1);
  events.get('load')();
  assert.equal(controls.length, 2);
});

test('an old map style callback cannot attach tools to a replacement map', () => {
  const { instance, events, controls, fits } = setup();
  const controlsBefore = controls.length;
  const fitsBefore = fits();
  const replacement = { addControl() { throw new Error('stale callback touched replacement'); } };
  instance.map = replacement;
  (events.get('style.load') || events.get('load'))();
  assert.equal(instance.map, replacement);
  assert.equal(controls.length, controlsBefore);
  assert.equal(fits(), fitsBefore);
});
