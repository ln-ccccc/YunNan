import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import { compile } from '@vue/compiler-dom';
import { compileScript, parse } from '@vue/compiler-sfc';
import * as Vue from 'vue';

import { createProjectWorkspaceApi } from '../src/projectWorkspace/projectWorkspaceApi.js';
import * as viewModel from '../src/projectWorkspace/projectWorkspaceViewModel.js';
import { sortInterpretationRecords } from '../src/utils/interpretationOrdering.js';

function setupComponent(filename, axios, extras = {}) {
  const source = fs.readFileSync(new URL(`../src/components/${filename}`, import.meta.url), 'utf8');
  const { descriptor } = parse(source);
  const dependencies = {
    ...Vue,
    ...viewModel,
    axios,
    createProjectWorkspaceApi,
    getSelectedProjectId: () => null,
    setSelectedProjectId: () => {},
    formatLifecycleStatus: (status) => status,
    sortInterpretationRecords,
    buildGeoViewEditorUrl: () => '',
    useProjectInference: () => ({
      inferenceRunning: Vue.ref(false), inferenceResult: Vue.ref(null), inferenceError: Vue.ref(''),
      imageryAssets: Vue.ref([]), imageryAssetsLoading: Vue.ref(false), imageryAssetsError: Vue.ref(''),
      loadImageryAssets: () => {}, runProjectInference: () => {},
    }),
    onMounted: () => {},
    onUnmounted: () => {},
    auditEnv: {},
    window: { location: { protocol: 'http:', hostname: 'localhost' } },
    ...extras,
  };
  for (const match of source.matchAll(/^import (\w+) from ['"].*\.vue['"];$/gm)) {
    dependencies[match[1]] = match[1];
  }
  const script = compileScript(descriptor, { id: filename }).content
    .replace(/^import[\s\S]*?;\s*/gm, '')
    .replaceAll('import.meta.env', 'auditEnv')
    .replace('export default', 'return');
  const component = new Function('dependencies', `with (dependencies) { ${script} }`)(dependencies);
  const emissions = [];
  const emit = (...args) => emissions.push(args);
  const bindings = component.setup({}, { expose: () => {}, emit });
  const context = Vue.proxyRefs({ ...bindings, $emit: emit });
  const render = new Function('Vue', compile(descriptor.template.content, {
    mode: 'function', prefixIdentifiers: true,
  }).code)({ ...Vue, resolveComponent: (name) => name, withDirectives: (node) => node });
  return { bindings, emissions, render: () => render(context, []) };
}

function findNode(node, predicate) {
  if (!node || typeof node !== 'object') return null;
  if (predicate(node)) return node;
  for (const child of Array.isArray(node.children) ? node.children : []) {
    const found = findNode(child, predicate);
    if (found) return found;
  }
  return null;
}

const flushRequests = () => new Promise((resolve) => setImmediate(resolve));

test('workspace shows a rejected historical basemap reason and keeps its current spatial data', async () => {
  const requests = [];
  const axios = {
    get: async (url) => {
      requests.push(url);
      return { data: { success: true, data: { items: [], features: [], jobs: [] } } };
    },
    post: async () => ({ status: 200, data: { success: false, code: 1, msg: '该底图未完成切片，请重试原任务' } }),
  };
  const { bindings } = setupComponent('ProjectWorkspace.vue', axios, {
    createProjectWorkspaceApi: (options) => createProjectWorkspaceApi({ ...options, http: axios }),
    window: { confirm: () => true },
  });
  bindings.currentProjectId.value = 24;
  await Vue.nextTick();
  await flushRequests();
  const spatial = { map_ready: true, resources: [{ id: 36, status: 'active' }] };
  bindings.slices.spatial.data = spatial;
  requests.length = 0;
  await bindings.reactivateBasemap(35);
  assert.match(bindings.slices.spatial.error, /未完成切片，请重试原任务/);
  assert.deepEqual(bindings.slices.spatial.data, spatial);
  assert.equal(bindings.projectOperationBusy.value, false);
  assert.deepEqual(requests, []);
});

test('interpretation keeps editor targets current after paging then switching projects', async () => {
  const axios = { get: async (path) => {
    if (path === '/api/projects') return { data: { data: { items: [] } } };
    if (path.endsWith('/classification-results')) {
      const projectId = Number(path.split('/')[3]);
      return { data: { data: { items: [
        { result_id: projectId * 10, mine_fid: projectId, year: 2026, vector_status: 'ready' },
      ] } } };
    }
    return { data: { data: [], count: 40 } };
  } };
  const { bindings } = setupComponent('SmartInterpretationView.vue', axios);
  bindings.selectedProjectId.value = 1;
  bindings.resetAndLoad();
  await flushRequests();
  assert.equal(bindings.editorTargets.value.get('1:2026')?.resultId, 10);
  bindings.changePage(2);
  await flushRequests();
  bindings.selectedProjectId.value = 2;
  bindings.resetAndLoad();
  await flushRequests();
  assert.equal(bindings.editorTargets.value.get('2:2026')?.resultId, 20);
  assert.equal(bindings.editorTargets.value.has('1:2026'), false);
});

test('interpretation map button emits the selected project id', () => {
  const harness = setupComponent('SmartInterpretationView.vue', {
    get: async () => ({ data: { data: { items: [] } } }),
  });
  harness.bindings.selectedProjectId.value = 7;
  const button = findNode(harness.render(), (node) => node.type === 'button'
    && node.children === '打开地图工作台');
  assert.ok(button);
  button.props.onClick();
  assert.deepEqual(harness.emissions, [['go-map', 7]]);
});

for (const kind of ['export', 'snapshot']) {
  test(`workspace ${kind} download event fetches and saves the project artifact`, async () => {
    const requests = [];
    const downloads = [];
    const axios = { get: async (path, options) => {
      requests.push({ path, options });
      return { data: new Blob(['test artifact']) };
    } };
    const harness = setupComponent('ProjectWorkspace.vue', axios, {
      document: {
        createElement: () => ({ click() { downloads.push(this.download); }, remove() {} }),
        body: { appendChild() {} },
      },
      URL: { createObjectURL: () => 'blob:audit', revokeObjectURL() {} },
    });
    harness.bindings.currentProjectId.value = 7;
    harness.bindings.slices.overview.data = { project_id: 7, summary: { id: 7 }, capabilities: {} };
    const panel = findNode(harness.render(), (node) => node.type === 'ProjectExportSnapshotPanel');
    assert.ok(panel);
    const listener = panel.props[kind === 'export' ? 'onDownloadExport' : 'onDownloadSnapshot'];
    assert.equal(typeof listener, 'function', '工作台必须处理子组件下载事件');
    await listener({ id: 4, artifact_name: 'project-export.geojson' });
    assert.deepEqual(requests, [{
      path: kind === 'export' ? '/api/projects/7/exports/4/artifact' : '/api/projects/7/backups/4/manifest',
      options: { responseType: 'blob' },
    }]);
    assert.deepEqual(downloads, [kind === 'export' ? 'project-export.geojson' : 'snapshot-4-manifest.json']);
  });
}

test('workspace clears a failed download message when retry succeeds', async () => {
  let fails = true;
  const harness = setupComponent('ProjectWorkspace.vue', {
    get: async () => {
      if (fails) throw new Error('下载暂不可用');
      return { data: new Blob(['retry artifact']) };
    },
  }, {
    document: { createElement: () => ({ click() {}, remove() {} }), body: { appendChild() {} } },
    URL: { createObjectURL: () => 'blob:audit', revokeObjectURL() {} },
  });
  harness.bindings.currentProjectId.value = 7;
  await harness.bindings.downloadExport({ id: 4 });
  assert.equal(harness.bindings.slices.exports.error, '下载暂不可用');
  assert.equal(harness.bindings.projectOperationBusy.value, false);
  fails = false;
  await harness.bindings.downloadExport({ id: 4 });
  assert.equal(harness.bindings.slices.exports.error, '');
  assert.equal(harness.bindings.projectOperationBusy.value, false);
});
