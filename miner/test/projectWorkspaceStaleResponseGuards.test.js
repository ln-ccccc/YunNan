import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

// 守护断言（问题16，2026-11-14）：历史底图与图斑清单的请求必须捕获
// 项目 ID 与请求代次、写入前核对；切项目立即清空旧数据；读取失败单独
// 展示，不得把"读取失败"伪装成"没有数据"；图斑筛选改变时重置页码。

const minerRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function readSource(relativePath) {
  return fs.readFileSync(path.join(minerRoot, relativePath), 'utf-8');
}

function extractFunctionSource(source, name) {
  let start = source.indexOf(`function ${name}`);
  if (start < 0) start = source.indexOf(`const ${name} =`);
  assert.ok(start >= 0, `源码应包含函数 ${name}`);
  const rest = source.slice(start);
  const next = rest.slice(1).search(/\n(async )?function |\nconst /);
  return next === -1 ? rest : rest.slice(0, next + 1);
}

test('ProjectWorkspace：历史底图请求写入前核对代次与项目 ID', () => {
  const source = readSource('src/components/ProjectWorkspace.vue');
  const fn = extractFunctionSource(source, 'loadRetainedBasemaps');
  assert.ok(fn.includes('retainedBasemapGate.next('), '应为历史底图请求建立自己的代次');
  assert.ok(fn.includes('retainedBasemapGate.isCurrent('), '写入前必须核对本请求代次');
  assert.ok(
    fn.includes('currentProjectId.value !== projectId'),
    '写入前必须核对项目 ID——A 项目慢响应不得覆盖 B 项目数据',
  );
});

test('ProjectWorkspace：历史底图读取失败单独展示，不再吞成空列表', () => {
  const source = readSource('src/components/ProjectWorkspace.vue');
  const fn = extractFunctionSource(source, 'loadRetainedBasemaps');
  assert.ok(
    fn.includes('retainedBasemapsError'),
    '失败应写入错误状态，不得把"读取失败"伪装成"没有历史底图"',
  );
  assert.ok(
    !/catch\s*\([^)]*\)\s*\{\s*retainedBasemaps\.value\s*=\s*\[\]/.test(fn),
    'catch 分支不得静默改写为空列表',
  );
  assert.ok(
    source.includes(':retained-error="retainedBasemapsError"'),
    '模板应把错误状态传给空间资源面板展示',
  );
});

test('ProjectWorkspace：切项目立即清空历史底图旧数据并作废在途请求', () => {
  const source = readSource('src/components/ProjectWorkspace.vue');
  const fn = extractFunctionSource(source, 'resetSpatialUiForProjectChange');
  assert.ok(fn.includes('retainedBasemapGate.next()'), '切项目应作废历史底图在途请求');
  assert.ok(fn.includes('retainedBasemaps.value = []'), '切项目应清空历史底图旧数据');
});

test('ParcelPanel：图斑清单请求写入前核对代次与项目 ID', () => {
  const source = readSource('src/components/projectWorkspace/ParcelPanel.vue');
  const fn = extractFunctionSource(source, 'loadSummary');
  assert.ok(fn.includes('requestGate.next('), '应为图斑清单请求建立自己的代次');
  assert.ok(fn.includes('requestGate.isCurrent('), '写入前必须核对本请求代次');
  assert.ok(
    fn.includes('props.projectId !== projectId'),
    '写入前必须核对项目 ID——A 项目慢响应不得覆盖 B 项目数据',
  );
});

test('ParcelPanel：切项目立即清空旧数据并作废在途请求', () => {
  const source = readSource('src/components/projectWorkspace/ParcelPanel.vue');
  const watch = source.slice(source.indexOf('watch(() => props.projectId'));
  assert.ok(watch.includes('requestGate.next()'), '切项目应作废图斑清单在途请求');
  assert.ok(watch.includes('items.value = []'), '切项目应清空图斑旧数据');
});

test('ParcelPanel：筛选条件改变时重置页码；越界页码纠正后重新请求', () => {
  const source = readSource('src/components/projectWorkspace/ParcelPanel.vue');
  assert.ok(
    source.includes('@keyup.enter="applyFilter"'),
    '筛选输入回车应走 applyFilter（重置页码），不得直接 loadSummary',
  );
  const fn = extractFunctionSource(source, 'applyFilter');
  assert.ok(fn.includes('page.value = 1'), '筛选改变必须从第一页重新请求——末页收窄筛选不得停留在越界页码');
  const load = extractFunctionSource(source, 'loadSummary');
  assert.ok(
    /page\.value = totalPages\.value;?\s*\n\s*loadSummary\(\)/.test(load),
    '页码越界纠正后应重新请求，列表与页码不得不一致',
  );
});
