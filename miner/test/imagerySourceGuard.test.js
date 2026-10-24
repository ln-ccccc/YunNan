import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const source = fs.readFileSync(new URL('../src/components/ImageryView.vue', import.meta.url), 'utf8');

test('imagery clip/slice payload uses dataset_id or storage_key, never file_path', () => {
  // 契约（AGENTS §6）：浏览器不得提交服务器物理路径；candidates DTO 已无 file_path（审查 P3-4 守护）
  assert.doesNotMatch(source, /selectedCandidate\.value\.file_path/, 'file_path 分支不得回潮');
  const sites = source.match(/source:\s*selectedCandidate\.value\.dataset_id[\s\S]{0,120}?storage_key/g) || [];
  assert.ok(sites.length >= 2, `clip/slice 两处 payload 均应为 dataset_id ?? storage_key，实际 ${sites.length} 处`);
});

test('loadCandidates discards stale responses when the project switches quickly', () => {
  // 竞态门控（2026-10-24 审查 P2-6）：A 项目响应回来时项目已切到 B，
  // 旧 dataset_id 不得留在 B 项目页上（跨项目资产引用）
  const fn = source.slice(source.indexOf('const loadCandidates'), source.indexOf('// ===== 手绘多边形'));
  assert.ok(fn.includes('const seq = (candidatesSeq ='), '请求前必须捕获独立计数器');
  assert.ok(fn.includes('const projectIdAtRequest ='), '请求前必须捕获项目 id');
  const guards = fn.split('if (seq !== candidatesSeq || projectIdAtRequest !== selectedProjectId.value) return;').length - 1;
  assert.equal(guards, 2, '成功/失败两分支都必须双检丢弃');
  assert.ok(fn.includes('if (seq === candidatesSeq) candidatesLoading.value = false;'), 'finally 复位 loading 必须仍持有上下文');
});
