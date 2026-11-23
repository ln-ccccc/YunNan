import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import { compile } from '@vue/compiler-dom';
import { compileScript, parse } from '@vue/compiler-sfc';
import * as Vue from 'vue';

import { createProjectWorkspaceApi } from '../src/projectWorkspace/projectWorkspaceApi.js';
import * as viewModel from '../src/projectWorkspace/projectWorkspaceViewModel.js';
import {
  exportOutcomePendingMessage,
  isExportOutcomeUncertain,
} from '../src/projectWorkspace/exportRequestFeedback.js';
import { shouldUseChunkedUpload } from '../src/utils/basemapChunking.js';
import { formatAreaKm2 } from '../src/utils/areaDisplay.js';
import { saveBlobAsFile } from '../src/utils/blobDownload.js';
import { sortInterpretationRecords } from '../src/utils/interpretationOrdering.js';

function setupComponent(filename, axios, extras = {}) {
  const source = fs.readFileSync(new URL(`../src/components/${filename}`, import.meta.url), 'utf8');
  const { descriptor } = parse(source);
  const dependencies = {
    ...Vue,
    ...viewModel,
    axios,
    createProjectWorkspaceApi,
    shouldUseChunkedUpload,
    saveBlobAsFile,
    formatAreaKm2,
    // 组件 import 的绑定都要在这里注入：下方 script 会剥掉 import、靠 with (dependencies) 解析
    isExportOutcomeUncertain,
    exportOutcomePendingMessage,
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

// 工作台下载经 saveBlobAsFile 触发。它只认浏览器全局（URL / document / setTimeout），且在
// 自己的模块作用域里解析，所以替身要装到 globalThis 上，不能走 setupComponent 的 extras；
// 真实定时器会把测试进程拖满 60 秒，一并替身掉。
function installDownloadFakes() {
  const downloads = [];
  const revoked = [];
  const timers = [];
  const originals = {
    createObjectURL: URL.createObjectURL,
    revokeObjectURL: URL.revokeObjectURL,
    document: globalThis.document,
    setTimeout: globalThis.setTimeout,
  };
  URL.createObjectURL = () => 'blob:audit';
  URL.revokeObjectURL = (url) => revoked.push(url);
  globalThis.document = {
    createElement: () => ({ click() { downloads.push(this.download); }, remove() {} }),
    body: { appendChild() {} },
  };
  globalThis.setTimeout = (callback, delay) => {
    timers.push({ callback, delay });
    return timers.length;
  };
  return {
    downloads,
    revoked,
    timers,
    restore: () => {
      URL.createObjectURL = originals.createObjectURL;
      URL.revokeObjectURL = originals.revokeObjectURL;
      globalThis.document = originals.document;
      globalThis.setTimeout = originals.setTimeout;
    },
  };
}

for (const kind of ['export', 'snapshot']) {
  test(`workspace ${kind} download event fetches and saves the project artifact`, async () => {
    const fakes = installDownloadFakes();
    try {
      const requests = [];
      const axios = { get: async (path, options) => {
        requests.push({ path, options });
        return { data: new Blob(['test artifact']) };
      } };
      const harness = setupComponent('ProjectWorkspace.vue', axios);
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
      assert.deepEqual(fakes.downloads, [kind === 'export' ? 'project-export.geojson' : 'snapshot-4-manifest.json']);
      // object URL 延迟释放：下载刚发起时不能释放，60 秒后才释放
      // （同步释放会截断尚未开始的下载，导出成果下载此前就是这么写的）
      assert.deepEqual(fakes.revoked, [], '下载发起后不得立刻释放 object URL');
      assert.equal(fakes.timers.length, 1);
      assert.equal(fakes.timers[0].delay, 60000);
      fakes.timers[0].callback();
      assert.deepEqual(fakes.revoked, ['blob:audit'], '到点释放本次下载的 object URL');
    } finally {
      fakes.restore();
    }
  });
}

test('workspace clears a failed download message when retry succeeds', async () => {
  const fakes = installDownloadFakes();
  try {
    let fails = true;
    const harness = setupComponent('ProjectWorkspace.vue', {
      get: async () => {
        if (fails) throw new Error('下载暂不可用');
        return { data: new Blob(['retry artifact']) };
      },
    });
    harness.bindings.currentProjectId.value = 7;
    await harness.bindings.downloadExport({ id: 4 });
    assert.equal(harness.bindings.slices.exports.error, '下载暂不可用');
    assert.equal(harness.bindings.projectOperationBusy.value, false);
    assert.deepEqual(fakes.timers, [], '失败的下载没有创建 object URL，也就没有待释放的定时器');
    fails = false;
    await harness.bindings.downloadExport({ id: 4 });
    assert.equal(harness.bindings.slices.exports.error, '');
    assert.equal(harness.bindings.projectOperationBusy.value, false);
  } finally {
    fakes.restore();
  }
});

// ---- 看板概览：同一时刻只发一个请求（2026-11-24）----
// 后端概览在缓存过期时是约 9 秒的纯计算，计算期间同一 worker 上的其它接口会被拖慢。工作台
// 首屏此前同一刻发两个相同请求（看板组件 onMounted + 工作台 loadProjects 各一次），等于让
// 两个 worker 同时重算。真实浏览器实测：同一毫秒发出两个请求各约 8.8 秒，其间其它接口最慢
// 约 670ms（平时约 27ms）。
test('stats panel keeps a single overview request in flight and refetches once for calls that arrived meanwhile', async () => {
  const pending = [];
  const harness = setupComponent('StatsOverviewPanel.vue', {
    get: () => new Promise((resolve) => pending.push(resolve)),
  });
  const first = harness.bindings.fetchOverview();
  const second = harness.bindings.fetchOverview();   // 在途期间又被调了一次
  await flushRequests();
  assert.equal(pending.length, 1, '在途请求未回来前不得再发第二个');
  assert.equal(harness.bindings.loading.value, true);
  pending[0]({ data: { data: { project_total: 5 } } });
  await flushRequests();
  assert.equal(pending.length, 2, '在途期间到达的调用（新建/归档后的刷新）不能被吞掉：补发一次');
  pending[1]({ data: { data: { project_total: 6 } } });
  await Promise.all([first, second]);
  await flushRequests();
  assert.equal(harness.bindings.overview.value.project_total, 6, '最终展示的是补发那次的数据');
  assert.equal(harness.bindings.loading.value, false);
  assert.equal(pending.length, 2, '补发一次即可，不得连锁再发');
});

test('stats panel sends a real request again once the previous one has returned', async () => {
  const responses = [{ data: { data: { project_total: 1 } } }, { data: { data: { project_total: 2 } } }];
  let calls = 0;
  const harness = setupComponent('StatsOverviewPanel.vue', {
    get: async () => { calls += 1; return responses[calls - 1]; },
  });
  await harness.bindings.fetchOverview();
  assert.equal(harness.bindings.overview.value.project_total, 1);
  await harness.bindings.fetchOverview();
  assert.equal(calls, 2, '上一个请求已回来，新的刷新必须真的发出去');
  assert.equal(harness.bindings.overview.value.project_total, 2);
});

test('stats panel shows the failure and recovers on the next call', async () => {
  let failing = true;
  const harness = setupComponent('StatsOverviewPanel.vue', {
    get: async () => {
      if (failing) throw new Error('502');
      return { data: { data: { project_total: 3 } } };
    },
  });
  await harness.bindings.fetchOverview();
  assert.equal(harness.bindings.error.value, '统计读取失败，请稍后刷新');
  assert.equal(harness.bindings.loading.value, false, '失败后加载态必须复位');
  failing = false;
  await harness.bindings.fetchOverview();
  assert.equal(harness.bindings.error.value, '');
  assert.equal(harness.bindings.overview.value.project_total, 3);
});

test('workspace first screen leaves the overview request to the stats panel', () => {
  const source = fs.readFileSync(new URL('../src/components/ProjectWorkspace.vue', import.meta.url), 'utf8');
  assert.match(source, /onMounted\(\(\) => loadProjects\(null, \{ refreshStats: false \}\)\)/, '首屏由看板组件自己拉，工作台不得再调一次');
  assert.match(source, /if \(refreshStats\) statsPanelRef\.value\?\.fetchOverview\?\.\(\)/, '新建/归档/手动刷新后的看板刷新必须保留');
});
