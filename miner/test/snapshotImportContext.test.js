import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import { compileScript, parse } from '@vue/compiler-sfc';
import * as Vue from 'vue';
import { createProjectWorkspaceApi as createRealWorkspaceApi } from '../src/projectWorkspace/projectWorkspaceApi.js';
import * as viewModel from '../src/projectWorkspace/projectWorkspaceViewModel.js';
import { shouldUseChunkedUpload } from '../src/utils/basemapChunking.js';
import { sortInterpretationRecords } from '../src/utils/interpretationOrdering.js';

function compileSetup(filename, dependencies) {
  const source = fs.readFileSync(new URL(`../src/components/${filename}`, import.meta.url), 'utf8');
  const { descriptor } = parse(source);
  for (const match of source.matchAll(/^import (\w+) from ['"].*\.vue['"];$/gm)) {
    dependencies[match[1]] = match[1];
  }
  const script = compileScript(descriptor, { id: filename }).content
    .replace(/^import[\s\S]*?;\s*/gm, '')
    .replaceAll('import.meta.env', 'auditEnv')
    .replace('export default', 'return');
  return new Function('dependencies', `with (dependencies) { ${script} }`)(dependencies);
}

function mountArchivePanel({ axiosMock, alerts }) {
  const dependencies = {
    ...Vue,
    axios: axiosMock,
    formatDateTime: () => 'time',
    formatActivityAction: (code) => code,
    auditEnv: {},
    window: { alert: (message) => alerts.push(message) },
  };
  const component = compileSetup('projectWorkspace/ProjectArchivePanel.vue', dependencies);
  const props = Vue.reactive({
    projectId: 1,
    assets: [],
    backups: [],
    activities: [],
    loading: false,
    error: '',
  });
  const emissions = [];
  const bindings = component.setup(props, { expose: () => {}, emit: (...args) => emissions.push(args) });
  return { bindings, props, emissions };
}

function mountWorkspace({ axiosMock, alerts }) {
  const dependencies = {
    ...Vue,
    ...viewModel,
    axios: axiosMock,
    createProjectWorkspaceApi: (options = {}) => createRealWorkspaceApi({ ...options, http: axiosMock }),
    shouldUseChunkedUpload,
    sortInterpretationRecords,
    getSelectedProjectId: () => null,
    setSelectedProjectId: () => {},
    formatLifecycleStatus: (status) => status,
    buildGeoViewEditorUrl: () => '',
    useProjectInference: () => ({
      inferenceRunning: Vue.ref(false), inferenceResult: Vue.ref(null), inferenceError: Vue.ref(''),
      imageryAssets: Vue.ref([]), imageryAssetsLoading: Vue.ref(false), imageryAssetsError: Vue.ref(''),
      loadImageryAssets: () => {}, runProjectInference: () => {},
    }),
    onMounted: () => {},
    onUnmounted: () => {},
    auditEnv: {},
    window: { location: { protocol: 'http:', hostname: 'localhost' }, alert: (m) => alerts.push(m) },
  };
  const component = compileSetup('ProjectWorkspace.vue', dependencies);
  const emissions = [];
  const bindings = component.setup({}, { expose: () => {}, emit: (...args) => emissions.push(args) });
  return { bindings };
}

const flush = () => new Promise((resolve) => setImmediate(resolve));

test('archive panel cancels snapshot import when project switches during file read', async () => {
  const alerts = [];
  const posts = [];
  const axiosMock = { post: (path, body) => { posts.push({ path, body }); return Promise.resolve({ data: {} }); } };
  const { bindings, props, emissions } = mountArchivePanel({ axiosMock, alerts });

  let resolveText;
  const delayedFile = { text: () => new Promise((resolve) => { resolveText = resolve; }) };
  const pending = bindings.handleImportFile({ target: { files: [delayedFile], value: 'x' } });

  props.projectId = 2;
  resolveText('{"project_name": "A"}');
  await pending;

  assert.equal(posts.length, 0, '项目切换后不得向任何项目提交导入请求');
  assert.equal(emissions.length, 0, '项目切换后不得触发导入事件');
  assert.ok(alerts.some((message) => message.includes('取消')), '应提示导入已取消');
  assert.equal(bindings.importing.value, false);
});

test('archive panel emits import with the project captured at operation start', async () => {
  const alerts = [];
  const axiosMock = { post: async () => ({ data: {} }) };
  const { bindings, props, emissions } = mountArchivePanel({ axiosMock, alerts });

  const manifest = { project_name: 'A' };
  await bindings.handleImportFile({ target: { files: [{ text: () => Promise.resolve(JSON.stringify(manifest)) }], value: 'x' } });

  props.projectId = 2;
  assert.deepEqual(emissions, [['import-snapshot', { projectId: 1, manifest }]],
    '导入事件必须携带操作开始时捕获的项目 ID，而不是可变的当前项目');
  assert.equal(alerts.length, 0, '未切换项目时不应弹取消提示');
});

test('workspace import targets the captured project even if selection switches mid-flight', async () => {
  const alerts = [];
  const posts = [];
  const gets = [];
  let resolvePost;
  const axiosMock = {
    get: async (path) => { gets.push(path); return { data: { data: {} } }; },
    post: (path, body) => { posts.push({ path, body }); return new Promise((resolve) => { resolvePost = resolve; }); },
  };
  const { bindings } = mountWorkspace({ axiosMock, alerts });
  bindings.currentProjectId.value = 1;

  const manifest = { project_name: 'A' };
  const pending = bindings.importSnapshot({ projectId: 1, manifest });
  bindings.currentProjectId.value = 2;
  resolvePost({ data: { data: {} } });
  await pending;
  await flush();

  assert.equal(posts.length, 1);
  assert.equal(posts[0].path, '/api/projects/1/backups/import', '导入必须发往操作开始时锁定的项目');
  assert.equal(gets.length, 0, '上下文已失效时不得触发新项目的刷新');
  assert.equal(alerts.length, 0, '成功提示不得显示在切换后的项目上');
  assert.equal(bindings.projectOperationBusy.value, false);
});

test('workspace drops import payloads bound to a different project', async () => {
  const alerts = [];
  const posts = [];
  const axiosMock = {
    get: async () => ({ data: { data: {} } }),
    post: (path) => { posts.push(path); return Promise.resolve({ data: {} }); },
  };
  const { bindings } = mountWorkspace({ axiosMock, alerts });
  bindings.currentProjectId.value = 1;

  await bindings.importSnapshot({ projectId: 9, manifest: { project_name: 'X' } });

  assert.equal(posts.length, 0, '载荷目标项目与当前项目不一致时必须丢弃');
  assert.equal(bindings.projectOperationBusy.value, false);
});
