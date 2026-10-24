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

// P1-6 保存腿上下文守卫（源码形状守护，与加载腿 reloadSeq 同构）：保存 A 期间
// 切到成果 B，A 的响应回来不得覆盖 B 的基准版本号/画布/编辑基线——服务端按
// base_revision_no 乐观锁写入 A 是合法状态，但本地 UI 必须丢弃。
test('saveDrawing guards UI updates against stale context', () => {
  const start = source.indexOf('async saveDrawing()');
  assert.ok(start > 0, 'saveDrawing 方法应存在');
  const body = source.slice(start, source.indexOf('async loadRevisions', start));

  const capture = body.indexOf('const seq = this.reloadSeq');
  const resultIdCapture = body.indexOf('this.context.resultId', 0);
  const awaitSave = body.indexOf('await saveClassificationRevision');
  assert.ok(capture > 0 && capture < awaitSave, '必须在 await 保存前捕获 reloadSeq');
  assert.ok(resultIdCapture > 0 && resultIdCapture < awaitSave, '必须在 await 保存前捕获 resultId');

  const guard = body.indexOf('if (!stillOwnsState()) return;');
  const resultUpdate = body.indexOf('this.result.current_feature_collection');
  assert.ok(
    guard > awaitSave && guard < resultUpdate,
    'await 之后的 UI 更新前必须先过 stillOwnsState 守卫',
  );

  const errorBranch = body.indexOf('if (!stillOwnsState()) return;', guard + 1);
  assert.ok(errorBranch > 0, '错误分支同样不得污染新上下文的提示');

  const finallyGuard = body.indexOf('if (stillOwnsState() || this.reloadSeq === -1)');
  assert.ok(finallyGuard > 0, 'finally 复位 saving 前必须确认仍持有上下文');
});
