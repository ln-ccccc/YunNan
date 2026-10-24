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
  const fn = source.slice(source.indexOf('const loadCandidates'), source.indexOf('// ===== 影像上手绘'));
  assert.ok(fn.includes('const seq = (candidatesSeq ='), '请求前必须捕获独立计数器');
  assert.ok(fn.includes('const projectIdAtRequest ='), '请求前必须捕获项目 id');
  const guards = fn.split('if (seq !== candidatesSeq || projectIdAtRequest !== selectedProjectId.value) return;').length - 1;
  assert.equal(guards, 2, '成功/失败两分支都必须双检丢弃');
  assert.ok(fn.includes('if (seq === candidatesSeq) candidatesLoading.value = false;'), 'finally 复位 loading 必须仍持有上下文');
});

test('hand-drawn clip is geographically bound to the selected imagery, not a fake bbox', () => {
  // 影像上手绘（2026-10-24 审查 P1-8）：旧实现用硬编码 RASTER_BBOX 把 SVG 归一化
  // 坐标换算成经纬度，画在哪张影像上全凭假想范围；守护三件事——假 bbox 不得回潮、
  // 底图必须是所选影像的预览叠加、顶点必须直接来自地图点击的真实经纬度
  assert.doesNotMatch(source, /RASTER_BBOX/, '硬编码假想 bbox 不得回潮（P1-8）');
  assert.ok(source.includes('L.imageOverlay('), '必须以所选影像的预览 PNG 作为绘制底图');
  assert.ok(source.includes('imagery/preview'), '必须从后端拉取影像预览');
  const geom = source.slice(source.indexOf('const drawGeometry'), source.indexOf('let previewSeq'));
  assert.ok(
    /\[\w+\.\w+\.toFixed\(6\),\s*\w+\.toFixed\(6\)\]/.test(geom) || geom.includes('toFixed(6)'),
    'GeoJSON 顶点须由地图真实经纬度构造',
  );
});

test('imagery preview fetch has its own race guard', () => {
  // 快速切换候选影像时旧预览不得覆盖新选中影像的底图（惯例同 loadCandidates）
  const fn = source.slice(source.indexOf('const loadPreview'), source.indexOf('const updateMineOverlay'));
  assert.ok(fn.includes('const seq = (previewSeq ='), '请求前必须捕获独立计数器');
  const guards = fn.split('if (seq !== previewSeq) return;').length - 1;
  assert.equal(guards, 2, '成功/失败两分支都必须丢弃过期响应');
  assert.ok(fn.includes('if (seq === previewSeq) previewLoading.value = false;'), 'finally 复位 loading 必须仍持有上下文');
});
