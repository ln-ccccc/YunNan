import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import * as projectWorkspaceViewModel from '../src/projectWorkspace/projectWorkspaceViewModel.js';

const minerRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function extractFunctionSource(source, name) {
  let start = source.indexOf(`function ${name}`);
  if (start < 0) start = source.indexOf(`const ${name} =`);
  assert.ok(start >= 0, `源码应包含函数 ${name}`);
  const rest = source.slice(start);
  const next = rest.slice(1).search(/\n(async )?function |\nconst /);
  return next === -1 ? rest : rest.slice(0, next + 1);
}

test('resolveProjectSelection：删除当前项目后选第一个剩余项目（问题17）', () => {
  const { resolveProjectSelection } = projectWorkspaceViewModel;
  assert.equal(
    resolveProjectSelection({ preferred: null, current: 5, items: [{ id: 9 }, { id: 7 }] }),
    9,
    '旧 currentProjectId 已被删除时，必须选新列表第一个剩余项目',
  );
});

test('resolveProjectSelection：删除全部项目返回 null（调用方须清空上下文）', () => {
  const { resolveProjectSelection } = projectWorkspaceViewModel;
  assert.equal(
    resolveProjectSelection({ preferred: null, current: 5, items: [] }),
    null,
  );
});

test('resolveProjectSelection：部分删除且当前项目仍在列表时保持不变', () => {
  const { resolveProjectSelection } = projectWorkspaceViewModel;
  assert.equal(
    resolveProjectSelection({ preferred: null, current: 5, items: [{ id: 7 }, { id: 5 }] }),
    5,
  );
});

test('resolveProjectSelection：preferred 存在于列表时优先；不存在时回落 current', () => {
  const { resolveProjectSelection } = projectWorkspaceViewModel;
  assert.equal(
    resolveProjectSelection({ preferred: 7, current: 5, items: [{ id: 5 }, { id: 7 }] }),
    7,
  );
  assert.equal(
    resolveProjectSelection({ preferred: 99, current: 5, items: [{ id: 5 }, { id: 7 }] }),
    5,
    'preferred 已不存在（如被并发删除）时回落到仍存在的当前项目',
  );
});

test('ProjectWorkspace：loadProjects 的选择候选必须经新列表成员校验', () => {
  const source = fs.readFileSync(
    path.join(minerRoot, 'src/components/ProjectWorkspace.vue'),
    'utf-8',
  );
  const fn = extractFunctionSource(source, 'loadProjects');
  assert.ok(
    fn.includes('resolveProjectSelection'),
    'loadProjects 应经 resolveProjectSelection 解析候选——不得直接沿用旧 currentProjectId',
  );
});

test('ProjectWorkspace：无剩余项目时清空当前上下文与共享选择状态', () => {
  const source = fs.readFileSync(
    path.join(minerRoot, 'src/components/ProjectWorkspace.vue'),
    'utf-8',
  );
  const fn = extractFunctionSource(source, 'loadProjects');
  assert.ok(
    fn.includes('setSelectedProjectId(null)'),
    '项目清空分支必须同步清空共享选择 store，其他模块页不得回选已删项目',
  );
});
