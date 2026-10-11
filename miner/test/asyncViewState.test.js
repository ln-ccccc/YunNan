import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc';
import * as Vue from 'vue';
import { renderToString } from '@vue/server-renderer';
import { buildGeoViewUrl, buildGeoViewEditorUrl } from '../src/navigation/geoviewNavigation.js';
import { createProjectWorkspaceApi } from '../src/projectWorkspace/projectWorkspaceApi.js';
import { createTileFallbackState, noteTileError, noteTileLoad } from '../src/map/tileFallbackPolicy.js';

const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const response = (data) => ({ data: { success: true, data } });
const location = { href: 'http://127.0.0.1:4000/', protocol: 'http:', hostname: '127.0.0.1' };

// 编译并运行真实 setup；仅隔离 HTTP 和浏览器边界，不替换被测视图逻辑。
function component(filename, http = {}, additions = {}) {
  const unmount = [];
  const opened = [];
  const dependencies = {
    ...Vue,
    axios: { get: async () => response({ items: [] }), ...http },
    getSelectedProjectId: () => null,
    setSelectedProjectId() {},
    formatLifecycleStatus: (value) => value,
    sortInterpretationRecords: (value) => value,
    buildGeoViewUrl,
    buildGeoViewEditorUrl,
    createTileFallbackState, noteTileError, noteTileLoad,
    useProjectInference: () => ({}),
    window: { location, alert() {}, open: (url) => opened.push(url) },
    localStorage: { getItem: () => null, setItem() {} },
    auditEnv: { VITE_GEOVIEW_URL: 'http://127.0.0.1:14613/segmentation' },
    onUnmounted: (callback) => unmount.push(callback),
    onBeforeUnmount: (callback) => unmount.push(callback),
    onMounted() {},
    ...additions,
  };
  const { descriptor } = parse(fs.readFileSync(new URL(`../src/components/${filename}`, import.meta.url), 'utf8'));
  for (const match of descriptor.scriptSetup.content.matchAll(/^import (\w+) from ['"].*\.vue['"];$/gm)) {
    dependencies[match[1]] = match[1];
  }
  const compiled = compileScript(descriptor, { id: filename });
  const script = compiled.content.replace(/^import[\s\S]*?;\s*/gm, '').replaceAll('import.meta.env', 'auditEnv').replace('export default', 'return');
  const scope = Vue.effectScope();
  const state = scope.run(() => new Function('dependencies', `with (dependencies) { ${script} }`)(dependencies).setup(additions.props || {}, { expose() {}, emit() {} }));
  return {
    state, opened,
    dispose() { unmount.forEach((callback) => callback()); scope.stop(); },
    async render() {
      const template = compileTemplate({ source: descriptor.template.content, filename, id: filename });
      const code = template.code.replace(/import \{([^}]+)\} from "vue"/, (_, names) => `const {${names.replaceAll(' as ', ': ')}} = Vue;`).replace('export function render', 'function render');
      const render = new Function('Vue', `${code}; return render;`)(Vue);
      return renderToString(Vue.createSSRApp({ setup: () => () => render(Vue.proxyRefs(state), []) }));
    },
  };
}

test('影像项目切换后旧候选、矿山和 finally 不覆盖当前请求', async () => {
  const old = deferred();
  const fresh = deferred();
  const view = component('ImageryView.vue', { get: (url) => {
    if (url.endsWith('/imagery/candidates')) return (url.includes('/1/') ? old : fresh).promise;
    if (url.endsWith('/geojson')) return Promise.resolve(response({ features: [{ properties: { FID_1: url.includes('/1/') ? 1 : 2 } }] }));
    return Promise.resolve(response({ items: [] }));
  } });
  const { state } = view;
  state.selectedProjectId.value = 1;
  const first = state.loadCandidates();
  state.selectedProjectId.value = 2;
  const second = state.loadCandidates();
  old.resolve(response({ items: [{ dataset_id: 1, display_name: '旧项目' }] }));
  await first;
  assert.equal(state.candidatesLoading.value, true);
  assert.deepEqual(state.candidates.value, []);
  fresh.resolve(response({ items: [{ dataset_id: 2, display_name: '当前项目' }] }));
  await second;
  assert.equal(state.candidates.value[0].dataset_id, 2);
  assert.deepEqual(state.mineFids.value, [2]);
  view.dispose();
});

test('旧影像失败及卸载后成功都不写入当前页面', async () => {
  const old = deferred();
  const view = component('ImageryView.vue', { get: (url) => url.endsWith('/imagery/candidates') ? old.promise : Promise.resolve(response({ items: [] })) });
  view.state.selectedProjectId.value = 1;
  const pending = view.state.loadCandidates();
  view.dispose();
  old.reject({ response: { data: { msg: '旧错误' } } });
  await pending;
  assert.equal(view.state.error.value, '');
});

test('手绘多边形使用当前影像 EPSG:4326 bounds 且无范围时不可裁剪', () => {
  const view = component('ImageryView.vue');
  const { state } = view;
  state.selectedCandidate.value = { dataset_id: 5, bounds: [102.44, 24.77, 102.46, 24.79] };
  state.drawPoints.value = [{ x: 0, y: 0 }, { x: 100, y: 0 }, { x: 100, y: 100 }];
  state.polygonClosed.value = true;
  assert.deepEqual(state.drawGeometry.value.coordinates[0], [[102.44, 24.79], [102.46, 24.79], [102.46, 24.77], [102.44, 24.79]]);
  state.selectedCandidate.value = { dataset_id: 6, bounds: null };
  assert.equal(state.clipReady.value, false);
  assert.equal(state.drawGeometry.value, null);
  view.dispose();
});

test('切换影像后不会把上一幅的手绘顶点用于下一幅', async () => {
  const view = component('ImageryView.vue');
  view.state.selectedCandidate.value = { dataset_id: 1, bounds: [1, 1, 2, 2] };
  await Vue.nextTick();
  view.state.drawPoints.value = [{ x: 10, y: 10 }, { x: 30, y: 10 }, { x: 30, y: 30 }];
  view.state.polygonClosed.value = true;
  view.state.selectedCandidate.value = { dataset_id: 2, bounds: [3, 3, 4, 4] };
  await Vue.nextTick();
  assert.equal(view.state.polygonClosed.value, false);
  assert.deepEqual(view.state.drawPoints.value, []);
  view.dispose();
});

test('裁剪失败的实际渲染有错误语义，不显示成功标记', async () => {
  const view = component('ImageryView.vue', { post: async () => { throw { response: { data: { msg: '影像文件不存在' } } }; } });
  view.state.selectedProjectId.value = 1;
  view.state.selectedCandidate.value = { dataset_id: 5 };
  view.state.clipSource.value = 'mine';
  view.state.clipMineFid.value = 7503;
  await view.state.runClip();
  const html = await view.render();
  assert.match(html, /class="result-line error"[^>]*>✗ 影像文件不存在<\/p>/);
  assert.doesNotMatch(html, /✓/);
  view.dispose();
});

test('旧项目裁剪回包不显示到新项目', async () => {
  const pending = deferred();
  const view = component('ImageryView.vue', { post: () => pending.promise });
  view.state.selectedProjectId.value = 1;
  view.state.selectedCandidate.value = { dataset_id: 5 };
  view.state.clipSource.value = 'mine';
  view.state.clipMineFid.value = 7503;
  const clip = view.state.runClip();
  view.state.selectedProjectId.value = 2;
  await view.state.loadCandidates();
  pending.resolve(response({ width: 50, height: 50, dataset_id: 7 }));
  await clip;
  assert.equal(view.state.clipResult.value, '');
  assert.equal(view.state.busy.value, null);
  view.dispose();
});

test('去解译链接使用已有 GeoView 配置地址', () => {
  const view = component('ImageryView.vue');
  view.state.selectedProjectId.value = 2;
  assert.equal(view.state.interpretHref.value, 'http://127.0.0.1:14613/segmentation?project_id=2');
  view.dispose();
});

test('同矿山同年记录按明确 result_id 打开和复核', async () => {
  const view = component('SmartInterpretationView.vue', { get: async (url) => response({ items: url.endsWith('/classification-results') ? [
    { result_id: 22, mine_fid: 1, year: 2024, vector_status: 'ready' },
    { result_id: 11, mine_fid: 1, year: 2024, vector_status: 'ready' },
  ] : [] }) });
  view.state.selectedProjectId.value = 1;
  await view.state.loadEditorTargets();
  const row = { data: { fid: 1, year: 2024, result_id: 22 } };
  view.state.openEditor(row);
  assert.equal(new URL(view.opened[0]).searchParams.get('result_id'), '22');
  view.state.openReview(row);
  assert.equal(view.state.reviewTarget.value.resultId, 22);
  view.dispose();
});

test('无编号的兼容记录稳定选择较新 result_id，明确未知编号不打开另一成果', async () => {
  for (const items of [
    [{ result_id: 22, mine_fid: 1, year: 2024, vector_status: 'ready' }, { result_id: 11, mine_fid: 1, year: 2024, vector_status: 'ready' }],
    [{ result_id: 11, mine_fid: 1, year: 2024, vector_status: 'ready' }, { result_id: 22, mine_fid: 1, year: 2024, vector_status: 'ready' }],
  ]) {
    const view = component('SmartInterpretationView.vue', { get: async (url) => response({ items: url.endsWith('/classification-results') ? items : [] }) });
    view.state.selectedProjectId.value = 1;
    await view.state.loadEditorTargets();
    view.state.openEditor({ data: { fid: 1, year: 2024 } });
    assert.equal(new URL(view.opened[0]).searchParams.get('result_id'), '22');
    view.state.openEditor({ data: { fid: 1, year: 2024, result_id: 999 } });
    assert.equal(view.opened.length, 1);
    view.dispose();
  }
});

test('后发查询和空关键词使前一次响应失效', async () => {
  const old = deferred();
  let requests = 0;
  const view = component('SearchView.vue', { get: () => ++requests === 1 ? old.promise : Promise.resolve(response({ items: [{ name: '乙项目' }] })) });
  view.state.keyword.value = '甲';
  const first = view.state.runSearch();
  view.state.keyword.value = '乙';
  await view.state.runSearch();
  old.resolve(response({ items: [{ name: '甲项目' }] }));
  await first;
  assert.equal(view.state.projectHits.value[0].name, '乙项目');
  const last = deferred();
  const empty = component('SearchView.vue', { get: () => last.promise });
  empty.state.keyword.value = '甲';
  const outstanding = empty.state.runSearch();
  empty.state.keyword.value = '';
  await empty.state.runSearch();
  last.resolve(response({ items: [{ name: '甲项目' }] }));
  await outstanding;
  assert.deepEqual(empty.state.projectHits.value, []);
  view.dispose(); empty.dispose();
});

test('矿山搜索第二次 await 和卸载后不污染新查询', async () => {
  const geo = deferred();
  const view = component('SearchView.vue', { get: (url) => url.endsWith('/geojson') ? geo.promise : Promise.resolve(response({ items: [{ id: 1, name: '甲', mine_count: 1 }] })) });
  view.state.keyword.value = '7503';
  const first = view.state.runSearch();
  await Vue.nextTick();
  await Vue.nextTick();
  view.state.keyword.value = '甲';
  await view.state.runSearch();
  geo.resolve(response({ features: [{ properties: { FID_1: 7503 } }] }));
  await first;
  assert.deepEqual(view.state.mineHits.value, []);
  const project = deferred();
  const gone = component('SearchView.vue', { get: () => project.promise });
  gone.state.keyword.value = '甲';
  const pending = gone.state.runSearch(); gone.dispose();
  project.resolve(response({ items: [{ name: '甲' }] }));
  await pending;
  assert.deepEqual(gone.state.projectHits.value, []);
  view.dispose();
});

function inference(http, projectId) {
  const script = fs.readFileSync(new URL('../src/composables/useProjectInference.js', import.meta.url), 'utf8').replace(/^import[\s\S]*?;\s*/gm, '').replaceAll('import.meta.env', 'auditEnv').replace('export function useProjectInference', 'function useProjectInference');
  const scope = Vue.effectScope();
  const api = scope.run(() => new Function('dependencies', `with (dependencies) { ${script}; return useProjectInference; }`)({
    ...Vue, auditEnv: {}, axios: http, pollInferenceJob() {},
    createProjectWorkspaceApi: (options) => createProjectWorkspaceApi({ ...options, http }),
  })(() => projectId.value));
  return { ...api, dispose: () => scope.stop() };
}

test('推理影像旧请求不关闭新请求 loading、不写入旧资产或错误', async () => {
  const old = deferred(); const fresh = deferred();
  const projectId = Vue.ref(1);
  const state = inference({ get: (url) => (url.includes('/1/') ? old : fresh).promise }, projectId);
  const first = state.loadImageryAssets();
  projectId.value = 2;
  const second = state.loadImageryAssets();
  old.reject(new Error('旧项目失败'));
  await first;
  assert.equal(state.imageryAssetsLoading.value, true);
  assert.equal(state.imageryAssetsError.value, '');
  fresh.resolve(response({ items: [{ name: '当前影像' }] }));
  await second;
  assert.equal(state.imageryAssets.value[0].name, '当前影像');
  state.dispose();
});

test('推理视图卸载后旧影像请求不继续写状态', async () => {
  const pending = deferred();
  const state = inference({ get: () => pending.promise }, Vue.ref(1));
  const request = state.loadImageryAssets();
  state.dispose();
  pending.resolve(response({ items: [{ name: '卸载后影像' }] }));
  await request;
  assert.deepEqual(state.imageryAssets.value, []);
});

test('稀疏项目瓦片初始 404 不移除影像层，后续有效瓦片仍可显示', () => {
  const layers = new Set();
  const fakeMap = { setView() { return this; }, setMaxZoom() {}, on() {}, removeLayer(layer) { layers.delete(layer); }, hasLayer: (layer) => layers.has(layer) };
  class TileLayer {
    constructor(url, options) { this.url = url; this.options = options; this.events = {}; }
    on(name, fn) { this.events[name] = fn; return this; }
    addTo() { layers.add(this); return this; }
  }
  const L = {
    TileLayer, map: () => fakeMap, tileLayer: (url, options) => new TileLayer(url, options),
    gridLayer: () => ({ addTo() { layers.add(this); return this; } }),
    control: { zoom: () => ({ addTo() {} }), scale: () => ({ addTo() {} }) },
  };
  const view = component('MapContainer.vue', {}, { L, props: { tileUrl: '/tiles/projects/1/1/{z}/{x}/{y}.png', minZoom: 8, maxZoom: 15, bounds: [102.44, 24.77, 102.46, 24.79] } });
  view.state.mapElement.value = {};
  view.state.initMap();
  const layer = [...layers][0];
  for (let i = 0; i < 6; i++) layer.events.tileerror?.();
  layer.events.tileload?.();
  assert.equal(layers.has(layer), true);
  assert.deepEqual(layer.options.bounds, [[24.77, 102.44], [24.79, 102.46]]);
});

test('真实矿山初始视野允许放大到 15 级而非锁在 12 级', () => {
  let options;
  const view = component('MapContainer.vue', {}, { L: {} });
  view.state.map.value = { fitBounds: (_, value) => { options = value; } };
  view.state.mineLayer.value = { getBounds: () => ({ isValid: () => true }) };
  view.state.fitMineLayerBounds();
  assert.equal(options.maxZoom, 15);
});

function leafletMethod(file, name) {
  const source = fs.readFileSync(new URL(`../node_modules/leaflet/src/${file}`, import.meta.url), 'utf8').replaceAll('\r\n', '\n');
  const body = source.split(`${name}: function () {`)[1].split('\n\t}\n});')[0];
  return new Function('DomUtil', `return function () {${body}};`)({ removeClass() {} });
}

test('卸载地图会取消 wheel 防抖及已进行的 Leaflet zoom 结束回调', async () => {
  const errors = [];
  const performZoom = leafletMethod('map/handler/Map.ScrollWheelZoom.js', '_performZoom');
  const endZoom = leafletMethod('map/Map.js', '_onZoomTransitionEnd');
  const fakeMap = {
    options: { wheelPxPerZoomLevel: 60, zoomSnap: 1 }, _animatingZoom: true, _tempFireZoomEvent: true,
    scrollWheelZoom: { disable() {}, _delta: 240, _lastMousePos: {} },
    getZoom: () => 12, _stop() {}, _limitZoom: (zoom) => zoom,
    setZoomAround() { if (this.removed) throw new Error('_leaflet_pos'); },
    _move() { if (this.removed) throw new Error('_updateLevels'); }, fire() {}, _moveEnd() {},
    stop() {}, off() {}, remove() { this.removed = true; },
  };
  fakeMap.scrollWheelZoom._map = fakeMap;
  fakeMap.scrollWheelZoom._timer = setTimeout(() => {
    try { performZoom.call(fakeMap.scrollWheelZoom); } catch (error) { errors.push(error.message); }
  }, 40);
  const transition = setTimeout(() => {
    try { endZoom.call(fakeMap); } catch (error) { errors.push(error.message); }
  }, 250);
  const view = component('MapContainer.vue', {}, { L: {} });
  view.state.map.value = fakeMap;
  view.dispose();
  await new Promise((resolve) => setTimeout(resolve, 280));
  clearTimeout(transition);
  assert.deepEqual(errors, []);
});
