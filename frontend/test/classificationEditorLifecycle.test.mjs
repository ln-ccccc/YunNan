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

// 保存竞态（2026-10-31 审计 P1，源码形状守护，行为断言见
// tests/browser/geoview-editor-save-race-smoke.cjs）：保存在途的本地新修改
// 不得被响应覆盖——代次+提交快照双比对必须在覆盖画布前执行；竞态分支只
// markSaved 登记新基线（撤销不穿越保存边界），不得 draw.set/重置历史。
test('saveDrawing detects edits made while the save request is in flight', () => {
  const start = source.indexOf('async saveDrawing()');
  assert.ok(start > 0, 'saveDrawing 方法应存在');
  const body = source.slice(start, source.indexOf('async loadRevisions', start));

  const snapshotCapture = body.indexOf('const submittedDrawJson = this.currentDrawJson()');
  const seqCapture = body.indexOf('const editSeq = this.editGeneration');
  const awaitSave = body.indexOf('await saveClassificationRevision');
  assert.ok(snapshotCapture > 0 && snapshotCapture < awaitSave, '必须在 await 保存前捕获提交快照');
  assert.ok(seqCapture > 0 && seqCapture < awaitSave, '必须在 await 保存前捕获编辑代次');

  const raceCheck = body.indexOf('this.editGeneration !== editSeq');
  const drawSet = body.indexOf('this.draw.set(');
  assert.ok(raceCheck > awaitSave && raceCheck < drawSet, '响应覆盖画布前必须先做代次+快照竞态比对');

  // 保存边界（2026-10-31 复审 P2）：提交后的首个 vertex 手势不得被 coalesce
  // 合并，否则 markSaved 找不到边界退化为清栈，保存期间的修改无法撤销
  const boundary = body.indexOf('this.history.markSubmitBoundary()');
  assert.ok(boundary > 0 && boundary < awaitSave, '必须在 await 保存前建立不可合并的提交边界');

  // 正式 ID 同步（2026-10-31 复审 P2）：client-* → manual-* 映射须在 markSaved
  // 前应用到画布与历史，否则再次保存把同一面当新面，破坏跨版本追踪
  const assignedMap = body.indexOf('buildAssignedFeatureIdMap(featureCollection, saved.feature_collection)');
  const applyIds = body.indexOf('this.applyFeatureIdMap(assignedIds)');
  const migrateIds = body.indexOf('this.history.migrateIds(assignedIds)');
  const markSaved = body.indexOf('this.history.markSaved(rewriteFeatureCollectionJson');
  assert.ok(assignedMap > raceCheck, '竞态分支必须构建服务端正式 ID 映射');
  assert.ok(applyIds > assignedMap && applyIds < markSaved, '正式 ID 必须先同步到画布');
  assert.ok(migrateIds > applyIds && migrateIds < markSaved, '正式 ID 必须同步到历史快照');
  assert.ok(markSaved > raceCheck && markSaved < drawSet, '竞态分支必须 markSaved 登记保存基线');

  // 竞态分支不得触碰画布与历史：draw.set 与 resetEditHistory 只允许出现在
  // 竞态比对之后（无新编辑的正常路径）
  assert.equal(
    body.indexOf('this.draw.set('),
    drawSet,
    '竞态比对与 draw.set 之间不得有其他 draw.set',
  );
  const resetHistory = body.indexOf('this.resetEditHistory()');
  assert.ok(resetHistory > raceCheck, 'resetEditHistory 只允许在竞态比对之后的正常路径');
});

// 冻结改类入口（2026-10-31 审计 P1 复现路径）：saving 期间改类按钮禁用，
// selectClass 双保险检查；编辑代次由 recordMutation 单一入口递增。
test('class change entry is frozen while saving and generation increments on mutation', () => {
  assert.match(
    source,
    /:disabled="saving"\s*\n\s*@click="selectClass/,
    '改类按钮必须在保存期间禁用',
  );

  const selectClassStart = source.indexOf('selectClass(classCode) {');
  assert.ok(selectClassStart > 0, 'selectClass 方法应存在');
  const selectClassHead = source.slice(selectClassStart, selectClassStart + 300);
  assert.match(selectClassHead, /if \(this\.saving\) return;/, 'selectClass 必须先检查 saving');

  const recordMutation = source.slice(
    source.indexOf('recordMutation(kind = \'edit\')'),
    source.indexOf('undoLastEdit()'),
  );
  assert.match(recordMutation, /this\.editGeneration \+= 1/, 'recordMutation 必须递增编辑代次');
});

// 底图 zoom 收敛（2026-10-31 审计 P2，源码形状守护，行为断言见
// tests/browser/geoview-editor-tile-zoom-smoke.cjs）：raster source 必须把
// manifest 声明的金字塔范围传给 resolveTileZoomRange，否则放大越级 404。
test('raster source applies the manifest zoom range via resolveTileZoomRange', () => {
  assert.match(
    source,
    /import \{[^}]*resolveTileZoomRange[^}]*\} from '@\/utils\/classificationEditor\.mjs'/,
    '编辑器必须引入 resolveTileZoomRange',
  );
  const sourceBuild = source.slice(source.indexOf('sources.project_basemap'), source.indexOf("layers.push({ id: 'project-basemap'"));
  assert.match(sourceBuild, /\.\.\.resolveTileZoomRange\(mapManifest\)/, 'raster source 必须展开 resolveTileZoomRange(mapManifest)');
});
