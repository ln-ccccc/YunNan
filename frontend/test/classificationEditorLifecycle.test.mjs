import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

// 源码形状守护（非行为验证）：分类成果编辑器的地图初始化必须发生在
// loading=false 之后——编辑器主体被 v-else-if="loading" 骨架屏门控，
// loading=true 期间 $refs.mapContainer 不存在，过早初始化会静默早退，
// 地图永不渲染（2026-09-20 GUI 实测发现的 P1，修复见 reloadResult 的 finally）。
const source = fs.readFileSync(
  fileURLToPath(new URL('../src/views/mainfun/ClassificationResultEditor.vue', import.meta.url)),
  'utf8',
);

test('editor initializes the map only after loading settles to false', () => {
  const callSites = [...source.matchAll(/this\.initializeMap\(\)/g)];
  assert.equal(callSites.length, 1, 'initializeMap 应只有一个调用点');
  const initIndex = callSites[0].index;

  const loadingFalse = source.indexOf('this.loading = false', source.indexOf('async reloadResult'));
  assert.ok(loadingFalse > 0, 'reloadResult 中应显式置 loading=false');
  assert.ok(
    loadingFalse < initIndex,
    'initializeMap 必须在 loading=false 之后调用（否则地图容器尚未挂载）',
  );
});
