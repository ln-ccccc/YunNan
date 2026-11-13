import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import * as projectWorkspaceViewModel from '../src/projectWorkspace/projectWorkspaceViewModel.js';

const minerRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function extractFunctionSource(source, name) {
  const start = source.indexOf(`function ${name}`);
  assert.ok(start >= 0, `源码应包含函数 ${name}`);
  const rest = source.slice(start);
  const next = rest.slice(1).search(/\n(async )?function /);
  return next === -1 ? rest : rest.slice(0, next + 1);
}

test('createSliceGates：局部刷新只作废目标分片，其余分片代次保持有效（问题15）', () => {
  const { createSliceGates } = projectWorkspaceViewModel;
  const gates = createSliceGates(['overview', 'assets']);
  const overviewToken = gates.next('overview');
  const assetsFirst = gates.next('assets');
  const assetsSecond = gates.next('assets');

  assert.equal(
    gates.isCurrent('overview', overviewToken),
    true,
    '刷新 assets 不应作废进行中的概览请求',
  );
  assert.equal(gates.isCurrent('assets', assetsFirst), false, '同分片旧请求必须失效');
  assert.equal(gates.isCurrent('assets', assetsSecond), true);
  assert.equal(gates.isCurrent('unknown', 1), false, '未注册分片一律视为非当前');
});

test('createSelectionGate 暴露 current() 读取当前代次（局部刷新免递增）', () => {
  const gate = projectWorkspaceViewModel.createSelectionGate();
  const token = gate.next();
  assert.equal(gate.current(), token);
  gate.next();
  assert.equal(gate.current(), token + 1);
});

test('ProjectWorkspace：局部刷新不再递增全局项目代次', () => {
  const source = fs.readFileSync(
    path.join(minerRoot, 'src/components/ProjectWorkspace.vue'),
    'utf-8',
  );
  const refreshCurrent = extractFunctionSource(source, 'refreshCurrent');
  assert.ok(
    !refreshCurrent.includes('beginProjectRequest'),
    'refreshCurrent 不应再走 beginProjectRequest 递增全局代次——那会使未请求分片（概览/活动）的进行中请求被丢弃且不重载',
  );
  assert.ok(
    refreshCurrent.includes('selectionGate.current()'),
    '局部刷新应以当前全局代次身份发起，不递增',
  );
});

test('ProjectWorkspace：分片写入必须同时校验全局代次与分片自身代次', () => {
  const source = fs.readFileSync(
    path.join(minerRoot, 'src/components/ProjectWorkspace.vue'),
    'utf-8',
  );
  const loadSlice = extractFunctionSource(source, 'loadSlice');
  assert.ok(loadSlice.includes('sliceGates.next('), 'loadSlice 应为分片递增自身代次');
  assert.ok(loadSlice.includes('sliceGates.isCurrent('), 'loadSlice 写入前应校验分片代次');
});
