import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import { compileScript, parse } from '@vue/compiler-sfc';
import * as Vue from 'vue';
import axios from 'axios';

import { createProjectWorkspaceApi } from '../src/projectWorkspace/projectWorkspaceApi.js';
import {
  exportOutcomePendingMessage,
  isExportOutcomeUncertain,
} from '../src/projectWorkspace/exportRequestFeedback.js';
import * as viewModel from '../src/projectWorkspace/projectWorkspaceViewModel.js';
import { shouldUseChunkedUpload } from '../src/utils/basemapChunking.js';

// 创建导出失败的前端容错（2026-11-23 D1）：后端同步生成，BFF 超时/网关 502/网络错误时
// 后端可能已落一条 completed 记录。失败分支必须刷新导出列表并给出不诱导重复提交的提示，
// 且不得破坏项目操作门控（切项目后旧请求的回调不写新项目状态）。

// 与 snapshotImportContext.test.js 的 compileSetup 同构：编译 <script setup>、剥掉 import 后
// 以 with (dependencies) 注入组件 import 的每个绑定，直接调用 setup() 返回的绑定
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

const flush = () => new Promise((resolve) => setImmediate(resolve));

// 构造 axios 在浏览器里抛出的错误形态（isAxiosError 标记 + response）
function axiosError({ status, data, message, code = 'ERR_NETWORK' } = {}) {
  const error = Object.assign(
    new Error(message || (status ? `Request failed with status code ${status}` : 'Network Error')),
    { isAxiosError: true, code },
  );
  if (status !== undefined) error.response = { status, data };
  return error;
}

// BFF 在上游超时/不可用时回的 502 信封：旧前端把它原样显示为「请稍后重试」，诱导重复提交
const gateway502 = () => axiosError({
  status: 502,
  data: { success: false, code: 1, msg: '上游服务不可用，请稍后重试' },
});

const PROJECT_ID = 7;
const EXPORT_PATH = `/api/projects/${PROJECT_ID}/exports`;
// 导出列表里一条「后端其实已经生成成功」的记录
const LATE_EXPORT = { id: 11, format: 'xlsx', status: 'completed', artifact_name: 'ledger.xlsx' };

const responseData = (path) => ({
  data: {
    data: path === EXPORT_PATH
      ? { items: [LATE_EXPORT] }
      : { items: [], capabilities: { can_export: true } },
  },
});

// 记录所有 GET 的 axios 替身；getData 可改写单个路径的行为
function createAxiosMock({ post, getData } = {}) {
  const gets = [];
  return {
    gets,
    get: async (path) => {
      gets.push(path);
      return getData ? getData(path) : responseData(path);
    },
    post,
  };
}

const exportsRefreshes = (gets, projectId = PROJECT_ID) => gets
  .filter((path) => path === `/api/projects/${projectId}/exports`).length;

function mountWorkspace(axiosMock) {
  const component = compileSetup('ProjectWorkspace.vue', {
    ...Vue,
    ...viewModel,
    axios: axiosMock,
    createProjectWorkspaceApi: (options = {}) => createProjectWorkspaceApi({ ...options, http: axiosMock }),
    shouldUseChunkedUpload,
    isExportOutcomeUncertain,
    exportOutcomePendingMessage,
    setSelectedProjectId: () => {},
    onMounted: () => {},
    onUnmounted: () => {},
    auditEnv: {},
    window: { location: { protocol: 'http:', hostname: 'localhost' } },
  });
  const bindings = component.setup({}, { expose: () => {}, emit: () => {} });
  bindings.currentProjectId.value = PROJECT_ID;
  return bindings;
}

function mountDataManager(axiosMock) {
  const component = compileSetup('DataManagerView.vue', {
    ...Vue,
    axios: axiosMock,
    getSelectedProjectId: () => null,
    setSelectedProjectId: () => {},
    formatLifecycleStatus: (status) => status,
    isExportOutcomeUncertain,
    exportOutcomePendingMessage,
    auditEnv: {},
  });
  const bindings = component.setup({}, { expose: () => {}, emit: () => {} });
  return bindings;
}

test('isExportOutcomeUncertain：5xx 与无响应的请求层错误算「可能已建档」，4xx 与本地普通 Error 不算', () => {
  // 结果不确定：请求可能已到达后端并仍在生成
  assert.equal(isExportOutcomeUncertain(axiosError({ status: 502 })), true);
  assert.equal(isExportOutcomeUncertain(axiosError({ status: 504 })), true);
  assert.equal(isExportOutcomeUncertain(axiosError({ status: 500 })), true);
  assert.equal(isExportOutcomeUncertain(axiosError()), true, '网络错误无响应');
  assert.equal(isExportOutcomeUncertain(axiosError({ message: 'timeout of 600000ms exceeded', code: 'ECONNABORTED' })), true);
  // 明确被拒：服务端以 4xx 拒绝，不会建档
  assert.equal(isExportOutcomeUncertain(axiosError({ status: 400 })), false);
  assert.equal(isExportOutcomeUncertain(axiosError({ status: 404 })), false);
  assert.equal(isExportOutcomeUncertain(axiosError({ status: 422 })), false);
  // 发请求前在本地抛出的普通 Error（载荷校验、2xx 信封 success:false 转成的 Error）
  assert.equal(isExportOutcomeUncertain(new Error('不支持 output_dir，请使用项目受控存储')), false);
  assert.equal(isExportOutcomeUncertain(new Error('项目请求失败')), false);
  assert.equal(isExportOutcomeUncertain(undefined), false);
});

test('exportOutcomePendingMessage：文案不诱导重试，并如实说明导出列表是否已刷新', () => {
  for (const refreshed of [true, false]) {
    const message = exportOutcomePendingMessage(refreshed);
    assert.ok(!message.includes('重试'), `不得出现「重试」：${message}`);
    assert.ok(message.includes('可能仍在生成'), message);
    assert.ok(message.includes('不要重复提交'), message);
    assert.notEqual(message, '上游服务不可用，请稍后重试');
  }
  assert.ok(exportOutcomePendingMessage(true).includes('已刷新导出列表'));
  assert.ok(!exportOutcomePendingMessage(true).includes('刷新失败'), '刷新成功时不得说失败');
  assert.ok(exportOutcomePendingMessage(false).includes('刷新失败'), '刷新失败时不得谎称已刷新');
  assert.ok(!exportOutcomePendingMessage(false).includes('已刷新导出列表'));
});

test('createExport 不带短于 BFF 10 分钟上限的浏览器侧超时（axios 默认 timeout=0 即不限）', async () => {
  // BFF 已把 createProjectExport 放宽到 10 分钟；浏览器侧若设了更短的 timeout 会先于 BFF 掐断
  const BFF_EXPORT_TIMEOUT_MS = 10 * 60 * 1000;
  assert.equal(axios.defaults.timeout, 0, 'axios 默认实例不设超时');

  const calls = [];
  const api = createProjectWorkspaceApi({
    http: {
      post(url, body, config) {
        calls.push({ url, body, config });
        return Promise.resolve({ data: { success: true, data: { id: 11 } } });
      },
    },
  });
  await api.createExport(10, { format: 'xlsx' });

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/projects/10/exports');
  assert.deepEqual(calls[0].body, { format: 'xlsx' });
  const timeout = calls[0].config?.timeout;
  assert.ok(
    timeout === undefined || timeout === 0 || timeout >= BFF_EXPORT_TIMEOUT_MS,
    `创建导出的浏览器侧超时不得短于 BFF 上限，实际为 ${timeout}`,
  );
});

test('项目工作台：创建导出 502 → 按成功路径同口径刷新（含导出列表），提示不诱导重复提交，busy 复位', async () => {
  const posts = [];
  const axiosMock = createAxiosMock({
    post: async (path, body) => { posts.push({ path, body }); throw gateway502(); },
  });
  const bindings = mountWorkspace(axiosMock);

  await bindings.createExport('xlsx');
  await flush();

  assert.deepEqual(posts, [{ path: EXPORT_PATH, body: { format: 'xlsx' } }]);
  assert.equal(exportsRefreshes(axiosMock.gets), 1, '失败后必须刷新导出列表');
  // 刷新范围与成功路径一致：INVALIDATION.export = overview / assets / exports / activity
  for (const suffix of ['/overview', '/assets', '/exports', '/timeline']) {
    assert.ok(axiosMock.gets.includes(`/api/projects/${PROJECT_ID}${suffix}`), `应刷新 ${suffix}`);
  }
  assert.deepEqual(bindings.slices.exports.data.items, [LATE_EXPORT], '后端已落库的记录应出现在刷新后的列表里');
  assert.equal(bindings.slices.exports.error, exportOutcomePendingMessage(true));
  assert.ok(!bindings.slices.exports.error.includes('重试'));
  assert.equal(bindings.slices.exports.loading, false);
  assert.equal(bindings.projectOperationBusy.value, false);
});

test('项目工作台：创建导出遇到网络错误（无响应）同样刷新列表并给出不诱导重试的提示', async () => {
  const axiosMock = createAxiosMock({ post: async () => { throw axiosError(); } });
  const bindings = mountWorkspace(axiosMock);

  await bindings.createExport('csv');
  await flush();

  assert.equal(exportsRefreshes(axiosMock.gets), 1);
  assert.equal(bindings.slices.exports.error, exportOutcomePendingMessage(true));
  assert.equal(bindings.projectOperationBusy.value, false);
});

test('项目工作台：创建导出被 4xx 明确拒绝 → 不刷新列表，沿用后端给出的拒绝原因', async () => {
  const axiosMock = createAxiosMock({
    post: async () => {
      throw axiosError({ status: 422, data: { success: false, code: 1, msg: '不支持的导出格式' } });
    },
  });
  const bindings = mountWorkspace(axiosMock);

  await bindings.createExport('dxf');
  await flush();

  assert.equal(exportsRefreshes(axiosMock.gets), 0, '服务端已明确拒绝，不会建档，无需刷新');
  assert.equal(bindings.slices.exports.error, '不支持的导出格式');
  assert.equal(bindings.projectOperationBusy.value, false);
});

test('项目工作台：发请求前的本地校验错误 → 不刷新列表，保留原始提示', async () => {
  const axiosMock = createAxiosMock({
    post: async () => { throw new Error('不支持 output_dir，请使用项目受控存储'); },
  });
  const bindings = mountWorkspace(axiosMock);

  await bindings.createExport('geojson');
  await flush();

  assert.equal(exportsRefreshes(axiosMock.gets), 0);
  assert.equal(bindings.slices.exports.error, '不支持 output_dir，请使用项目受控存储');
  assert.equal(bindings.projectOperationBusy.value, false);
});

test('项目工作台：创建导出成功 → 行为不变（刷新一次、无错误提示）', async () => {
  const axiosMock = createAxiosMock({
    post: async () => ({ data: { success: true, code: 0, data: { id: 11 } } }),
  });
  const bindings = mountWorkspace(axiosMock);

  await bindings.createExport('xlsx');
  await flush();

  assert.equal(exportsRefreshes(axiosMock.gets), 1);
  assert.deepEqual(bindings.slices.exports.data.items, [LATE_EXPORT]);
  assert.equal(bindings.slices.exports.error, '');
  assert.equal(bindings.projectOperationBusy.value, false);
});

test('项目工作台：导出请求未返回时切换项目 → 旧请求失败后不刷新、不写提示、不破坏门控', async () => {
  let rejectPost;
  const axiosMock = createAxiosMock({
    post: () => new Promise((_, reject) => { rejectPost = reject; }),
  });
  const bindings = mountWorkspace(axiosMock);

  const pending = bindings.createExport('xlsx');
  await bindings.selectProject(8); // 用户在导出请求未返回时切到项目 8
  await flush();
  const requestsAfterSwitch = axiosMock.gets.length;

  rejectPost(gateway502());
  await pending;
  await flush();

  // 刷新的是「当前项目」：旧回调若漏过门控会去刷新项目 8，并打断项目 8 正在进行的加载
  assert.equal(axiosMock.gets.length, requestsAfterSwitch, '旧请求的失败回调不得触发任何项目的刷新');
  assert.equal(bindings.slices.exports.error, '', '不得把旧项目的提示写进新项目');
  assert.equal(bindings.currentProjectId.value, 8);
  assert.equal(bindings.projectOperationBusy.value, false, '门控不得被旧请求的 finally 改坏');
});

test('项目工作台：失败后的列表刷新期间切换项目 → 不把提示写进新项目', async () => {
  let releaseGets;
  const gate = new Promise((resolve) => { releaseGets = resolve; });
  const axiosMock = createAxiosMock({
    post: async () => { throw gateway502(); },
    getData: async () => { await gate; return responseData(EXPORT_PATH); },
  });
  const bindings = mountWorkspace(axiosMock);

  const pending = bindings.createExport('xlsx');
  await flush();
  assert.equal(exportsRefreshes(axiosMock.gets), 1, '失败后应已发起导出列表刷新');

  const switching = bindings.selectProject(8); // 刷新未完成时切到项目 8
  releaseGets();
  await Promise.all([pending, switching]);
  await flush();

  assert.equal(bindings.slices.exports.error, '', '刷新期间切了项目，旧项目的提示不得写入');
  assert.equal(bindings.currentProjectId.value, 8);
  assert.equal(bindings.projectOperationBusy.value, false);
});

test('项目工作台：失败后的列表刷新也失败 → 文案如实说明刷新失败，且仍不诱导重试', async () => {
  const axiosMock = createAxiosMock({
    post: async () => { throw gateway502(); },
    getData: async (path) => {
      if (path === EXPORT_PATH) throw axiosError();
      return responseData(path);
    },
  });
  const bindings = mountWorkspace(axiosMock);

  await bindings.createExport('xlsx');
  await flush();

  assert.equal(bindings.slices.exports.error, exportOutcomePendingMessage(false));
  assert.ok(!bindings.slices.exports.error.includes('已刷新导出列表'));
  assert.equal(bindings.projectOperationBusy.value, false);
});

test('数据管理页：创建导出 502 → 重新读取项目数据，提示不诱导重复提交，busy 复位', async () => {
  const posts = [];
  const axiosMock = createAxiosMock({
    post: async (path, body) => { posts.push({ path, body }); throw gateway502(); },
  });
  const bindings = mountDataManager(axiosMock);
  await flush(); // setup 末尾的 loadProjects
  bindings.selectedProjectId.value = PROJECT_ID;
  assert.equal(exportsRefreshes(axiosMock.gets), 0);

  await bindings.createExport('xlsx');
  await flush();

  assert.deepEqual(posts, [{ path: EXPORT_PATH, body: { format: 'xlsx' } }]);
  for (const suffix of ['/overview', '/exports', '/backups']) {
    assert.ok(axiosMock.gets.includes(`/api/projects/${PROJECT_ID}${suffix}`), `失败后应重新读取 ${suffix}`);
  }
  assert.equal(exportsRefreshes(axiosMock.gets), 1);
  assert.deepEqual(bindings.exports.value, [LATE_EXPORT], '后端已落库的记录应出现在重新读取后的列表里');
  assert.equal(bindings.operationError.value, exportOutcomePendingMessage(true));
  assert.ok(!bindings.operationError.value.includes('重试'));
  assert.equal(bindings.busy.value, false);
});

test('数据管理页：创建导出被 4xx 明确拒绝 → 不重新读取，沿用后端给出的拒绝原因', async () => {
  const axiosMock = createAxiosMock({
    post: async () => {
      throw axiosError({ status: 422, data: { success: false, code: 1, msg: '不支持的导出格式' } });
    },
  });
  const bindings = mountDataManager(axiosMock);
  await flush();
  bindings.selectedProjectId.value = PROJECT_ID;

  await bindings.createExport('dxf');
  await flush();

  assert.equal(exportsRefreshes(axiosMock.gets), 0);
  assert.equal(bindings.operationError.value, '不支持的导出格式');
  assert.equal(bindings.busy.value, false);
});

test('数据管理页：创建导出成功 → 行为不变（读取一次、无错误提示）', async () => {
  const axiosMock = createAxiosMock({
    post: async () => ({ data: { success: true, code: 0, data: { id: 11 } } }),
  });
  const bindings = mountDataManager(axiosMock);
  await flush();
  bindings.selectedProjectId.value = PROJECT_ID;

  await bindings.createExport('xlsx');
  await flush();

  assert.equal(exportsRefreshes(axiosMock.gets), 1);
  assert.deepEqual(bindings.exports.value, [LATE_EXPORT]);
  assert.equal(bindings.operationError.value, '');
  assert.equal(bindings.busy.value, false);
});

test('数据管理页：导出请求未返回时切换项目 → 旧请求失败后不触发重新读取、不写提示', async () => {
  let rejectPost;
  const axiosMock = createAxiosMock({
    post: () => new Promise((_, reject) => { rejectPost = reject; }),
  });
  const bindings = mountDataManager(axiosMock);
  await flush(); // setup 末尾的 loadProjects
  bindings.selectedProjectId.value = PROJECT_ID;

  const pending = bindings.createExport('xlsx');
  bindings.selectedProjectId.value = 8;
  bindings.resetAndLoad(); // 用户在导出请求未返回时切到项目 8
  await flush();
  const requestsAfterSwitch = axiosMock.gets.length;

  rejectPost(gateway502());
  await pending;
  await flush();

  // 重新读取的是「当前项目」：旧回调若漏过门控会去重读项目 8，并打断项目 8 正在进行的读取
  assert.equal(axiosMock.gets.length, requestsAfterSwitch, '旧请求的失败回调不得触发任何项目的重新读取');
  assert.equal(bindings.operationError.value, '', '不得把旧项目的提示写进新项目视图');
  assert.equal(bindings.selectedProjectId.value, 8);
  assert.equal(bindings.busy.value, false);
});

test('数据管理页：失败后的重新读取期间切换项目 → 不把旧项目的提示写进新项目视图', async () => {
  let releaseGets;
  const gate = new Promise((resolve) => { releaseGets = resolve; });
  const axiosMock = createAxiosMock({
    post: async () => { throw gateway502(); },
    getData: async (path) => { await gate; return responseData(path); },
  });
  const bindings = mountDataManager(axiosMock);

  bindings.selectedProjectId.value = PROJECT_ID;
  const pending = bindings.createExport('xlsx');
  await flush(); // 失败分支已进入 loadProjectData（等待 gate）

  bindings.selectedProjectId.value = 8;
  bindings.resetAndLoad(); // 用户切到项目 8
  releaseGets();
  await pending;
  await flush();
  await flush();

  assert.equal(bindings.operationError.value, '', '旧项目的导出提示不得出现在新项目视图');
  assert.equal(bindings.selectedProjectId.value, 8);
  assert.equal(bindings.busy.value, false);
});

test('数据管理页：失败后的重新读取也失败 → 文案如实说明刷新失败', async () => {
  const axiosMock = createAxiosMock({
    post: async () => { throw gateway502(); },
    getData: async (path) => {
      if (path === EXPORT_PATH) throw axiosError();
      return responseData(path);
    },
  });
  const bindings = mountDataManager(axiosMock);
  await flush();
  bindings.selectedProjectId.value = PROJECT_ID;

  await bindings.createExport('xlsx');
  await flush();

  assert.equal(bindings.operationError.value, exportOutcomePendingMessage(false));
  assert.equal(bindings.busy.value, false);
});
