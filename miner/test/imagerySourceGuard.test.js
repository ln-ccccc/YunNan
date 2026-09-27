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
