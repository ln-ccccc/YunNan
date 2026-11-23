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

// ─── 2026-11-23 缺陷修复的源码形状守护 ────────────────────────────────────────
// 以上既有守护在本次改造后原样通过，未做任何调整；下面是新增项。
// 组件依赖 maplibre-gl / DOM，无法在 node:test 里实例化，组件侧沿用"源码形状守护"；
// 可脱离 DOM 的判定逻辑都在 utils/*.mjs 里，行为断言见对应的 *.test.mjs。

/** 取 [startMarker, endMarker) 之间的源码片段，任一标记缺失即失败 */
function sliceBetween(startMarker, endMarker) {
  const start = source.indexOf(startMarker);
  assert.ok(start >= 0, `源码中应存在：${startMarker}`);
  const end = source.indexOf(endMarker, start + startMarker.length);
  assert.ok(end > start, `${startMarker} 之后应存在：${endMarker}`);
  return source.slice(start, end);
}

/** 去掉整行注释与块注释：守护只看代码，注释里提到的 Promise.all / await 不算数 */
function withoutComments(code) {
  return code.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^[ \t]*\/\/.*$/gm, '');
}

test('vertex deletion guard is registered on the real direct_select entry (A1)', () => {
  // 模式经官方扩展点注册：保留全部上游模式，只接管 direct_select
  assert.match(
    source,
    /modes: \{\s*\.\.\.MapboxDraw\.modes,\s*direct_select: createGuardedDirectSelect\(\s*MapboxDraw\.modes\.direct_select,/,
    '必须用带防护的工厂替换 direct_select，其余模式原样保留',
  );
  // 拦截文案由纯函数生成，连按不堆叠
  assert.match(
    source,
    /this\.\$message\.warning\(describeVertexDeletionBlockers\(blockers, geometry\), \{ grouping: true \}\)/,
  );
  // 旧的"数当前顶点数"函数及其容器捕获拦截已删除
  assert.doesNotMatch(source, /polygonVertexCount/, 'polygonVertexCount 已被 vertexDeletionBlockers 取代');
  const clickGuard = source.slice(source.indexOf("'click',"), source.indexOf('// Esc 退出 direct_select'));
  assert.match(clickGuard, /if \(!hasSelectedVertex\) \{\s*\n\s*this\.\$message\.info\(/, '无选中顶点的引导提示保留');
  assert.doesNotMatch(
    clickGuard,
    /stopPropagation|preventDefault/,
    '容器捕获监听只负责引导提示，不再在点击路上拦截删除',
  );
  // Esc 退出顶点模式的 keydown 监听保留不动
  assert.match(
    source,
    /this\.map\.getContainer\(\)\.addEventListener\(\s*'keydown',\s*\(e\) => \{\s*if \(e\.key !== 'Escape' \|\| !this\.draw \|\| this\.draw\.getMode\(\) !== 'direct_select'\) return;/,
  );
});

test('reloadResult no longer waits for the revision list before the canvas is ready (A2)', () => {
  const body = withoutComments(sliceBetween('async reloadResult()', 'initializeMap() {'));
  assert.equal((body.match(/await getClassificationResult\(/g) || []).length, 1, '成果详情是首屏唯一等待的网络请求');
  assert.doesNotMatch(body, /Promise\.all\(/, '不得再把详情与版本列表绑在 Promise.all 里');
  assert.doesNotMatch(body, /getClassificationRevisions\(/, '版本列表只能经 loadRevisions 发出');
  assert.doesNotMatch(body, /await\s+this\.loadRevisions/, '版本列表刷新不得被 await');
  // 首屏的全部挂起点就这三处：离开确认框、成果详情、地图初始化前的 nextTick
  assert.deepEqual(
    [...body.matchAll(/await\s+[\w.$]+\([^)\n]*\)/g)].map((match) => match[0]),
    [
      'await this.confirmDiscardUnsaved()',
      'await getClassificationResult(this.context.projectId, this.context.resultId)',
      'await this.$nextTick()',
    ],
  );

  // 详情到手即装配画布状态，随后才发出（不阻塞的）版本请求
  const assign = body.indexOf('this.result = result;');
  const coverage = body.indexOf('this.coverageNotice = basemapCoverageNotice(');
  const request = body.indexOf('this.loadRevisions().catch(');
  assert.ok(assign > 0 && coverage > assign && request > coverage, '顺序应为：写入 result → 覆盖提示 → 版本请求');

  // 版本接口失败的提示同样要过会话归属
  assert.match(
    body.slice(request),
    /this\.loadRevisions\(\)\.catch\(\(\) => \{\s*if \(seq !== this\.reloadSeq \|\| seq === -1\) return;\s*this\.\$message\.warning\(/,
  );
});

test('loadRevisions writes the revision list only when the whole editing session is unchanged (A2)', () => {
  const body = sliceBetween('async loadRevisions()', 'locateSaveValidationFailure(error');
  const request = body.indexOf('await getClassificationRevisions(');
  assert.ok(request > 0, '应发出 revisions 请求');
  // 发出之前捕获：会话代次、请求序号、项目、成果
  for (const capture of [
    'const { projectId, resultId } = this.context;',
    'const sessionSeq = this.reloadSeq;',
    'this.revisionsLoadSeq = (this.revisionsLoadSeq || 0) + 1',
  ]) {
    const at = body.indexOf(capture);
    assert.ok(at >= 0 && at < request, `必须在 await 之前捕获：${capture}`);
  }
  // 返回之后、写入之前四项逐一比对，任一不符即返回 false 且不写状态
  const write = body.indexOf('this.revisions = payload?.revisions || [];');
  assert.ok(write > request, '归属检查必须先于写 revisions');
  const check = body.slice(request, write);
  assert.match(check, /sessionSeq !== this\.reloadSeq/, '会话代次：同一成果重新加载后旧响应也作废');
  assert.match(check, /seq !== this\.revisionsLoadSeq/, '请求序号：只认最新一次请求');
  assert.match(check, /current\.projectId !== projectId/, '项目');
  assert.match(check, /current\.resultId !== resultId/, '成果');
  assert.match(check, /return false;/);
  assert.match(body.slice(write), /return true;/);
  // loading 只由"仍是当前会话的最新请求"收起
  assert.match(
    body.slice(0, request) + body.slice(body.indexOf('} finally {', request), write),
    /if \(seq === this\.revisionsLoadSeq && sessionSeq === this\.reloadSeq\) \{\s*this\.revisionsLoading = false;/,
  );
});

test('saveDrawing releases the save lock before refreshing revisions and never awaits it (A2)', () => {
  const body = withoutComments(sliceBetween('async saveDrawing()', 'async loadRevisions'));
  const refreshes = [...body.matchAll(/this\.loadRevisions\(\)\.catch\(\(\) => \{/g)];
  assert.equal(refreshes.length, 2, '两条成功分支各刷新一次版本列表');
  assert.doesNotMatch(body, /await\s+this\.loadRevisions/, '版本刷新不得阻塞保存流程');
  // 整个 saveDrawing 只 await 保存请求这一次：保存成功之后的所有分支都不再挂起
  assert.equal((body.match(/\bawait\b/g) || []).length, 1, '只应 await saveClassificationRevision');

  // 每条成功分支都先把 saving 置回 false，再发出刷新
  let previousEnd = 0;
  for (const refresh of refreshes) {
    assert.ok(
      body.slice(previousEnd, refresh.index).includes('this.saving = false;'),
      '发出刷新之前必须已解除 saving',
    );
    previousEnd = refresh.index + refresh[0].length;
    // 刷新失败的提示同样要过会话归属
    const handler = body.slice(refresh.index, body.indexOf('});', refresh.index));
    assert.match(handler, /if \(!stillOwnsState\(\)\) return;\s*this\.\$message\.warning\(/);
  }

  // 竞态分支：从判定到 return 没有任何挂起，"已保存…请再次保存"不会被归属检查吞掉
  const raceStart = body.indexOf('this.editGeneration !== editSeq');
  assert.ok(raceStart > 0 && raceStart < refreshes[0].index);
  const raceBranch = body.slice(raceStart, refreshes[0].index);
  assert.match(
    raceBranch,
    /this\.\$message\.warning\(`已保存为 V\$\{saved\.current_revision_no\}；保存期间又有新的修改，请再次保存。`\);/,
  );
  // 正常分支：成功提示同样先于刷新
  const normalBranch = body.slice(refreshes[0].index + refreshes[0].length, refreshes[1].index);
  assert.match(normalBranch, /this\.\$message\.success\(`已保存为 V\$\{saved\.current_revision_no\}`\);/);
  assert.match(normalBranch, /this\.\$message\.success\(`[^`]*`\);[^\n]*\n\s*this\.saving = false;/, '先提示、再解除 saving');
});

test('revision panel shows a loading state instead of "no revisions" while loading (A2)', () => {
  assert.match(
    source,
    /<div v-if="revisionsLoading" class="revision-loading">版本记录加载中…<\/div>\s*\n\s*<el-empty v-else-if="!revisions\.length" description="暂无版本记录"/,
  );
  assert.match(source, /revisionsLoading: false,/, 'revisionsLoading 应声明为响应式状态');
});

test('focus bounds fall back from features to ROI to manifest (E1)', () => {
  assert.match(
    source,
    /import \{[^}]*resolveFocusBounds[^}]*\} from '@\/utils\/classificationEditor\.mjs'/,
    '编辑器必须引入 resolveFocusBounds',
  );
  assert.match(
    sliceBetween('async reloadResult()', 'initializeMap() {'),
    /basemapCoverageNotice\(\s*resolveFocusBounds\(result\?\.current_feature_collection, result\?\.roi_geometry, manifestBounds\),\s*manifestBounds,\s*\)/,
    '底图覆盖提示按同一优先级取关注范围',
  );
  const fit = sliceBetween('fitToCurrentFeatures(manifestBounds = null) {', 'resetDrawing() {');
  assert.match(fit, /resolveFocusBounds\(featureCollection, this\.result\?\.roi_geometry, null\)/);
  assert.match(
    fit,
    /contentBounds \? \{ padding: 64, maxZoom: 16, duration: 0 \} : \{ padding: 64, maxZoom: 15, duration: 0 \}/,
    '图斑与 ROI 用 64/16，manifest 保持 64/15',
  );
  assert.doesNotMatch(source, /featureBounds\(|LngLatBounds/, '旧的"无图斑即回退整张底图"定位已移除');
});

test('discarding all edits asks for confirmation first (E2-a)', () => {
  assert.match(
    source,
    /<el-button :disabled="!isDirty \|\| saving" plain @click="discardAllEdits">放弃全部修改<\/el-button>/,
    '按钮的禁用条件不变，点击改为走确认流程',
  );
  assert.doesNotMatch(source, /@click="resetDrawing"/, '不得再直接绑定 resetDrawing');
  const body = sliceBetween('async discardAllEdits() {', 'currentDrawJson() {');
  assert.match(body, /if \(!this\.isDirty \|\| !this\.draw\) return;/, '非脏状态直接返回');
  assert.match(
    body,
    /this\.\$confirm\(\s*'[^']*无法恢复[^']*',\s*'放弃修改',\s*\{ confirmButtonText: '放弃修改', cancelButtonText: '继续编辑', type: 'warning' \},?\s*\)/,
  );
  const cancel = body.indexOf('if (!confirmed) return;');
  const recheck = body.indexOf('if (seq !== this.reloadSeq || !this.isDirty) return;');
  const reset = body.indexOf('this.resetDrawing();');
  assert.ok(cancel > 0 && recheck > cancel && reset > recheck, '取消则不动；确认后先复核会话与脏状态再重置');
  assert.equal((body.match(/this\.resetDrawing\(\);/g) || []).length, 1);
});

test('merge button is disabled for mixed-class selections (E2-b)', () => {
  assert.match(
    source,
    /<el-button\s*:disabled="selectedDrawIds\.length < 2 \|\| mixedClassSelection \|\| saving"\s*:title="mixedClassSelection \? '仅支持合并同地类的面' : '[^']*'"\s*plain\s*@click="mergeSelectedFeatures"\s*>\s*合并选中\s*<\/el-button>/,
  );
  assert.match(source, /mixedClassSelection\(\) \{\s*return this\.selectedClassCodes\.length > 1;/);
  // selectedDrawIds 只有一个写入口，写入口同步刷新地类集合（不会出现过期地类）
  const writers = [...source.matchAll(/this\.selectedDrawIds = /g)];
  assert.equal(writers.length, 1, 'this.selectedDrawIds 只能在 setSelectedDrawIds 里赋值');
  const writer = sliceBetween('setSelectedDrawIds(drawIds) {', 'setSelectedDrawIds([]);');
  assert.match(writer, /this\.selectedDrawIds = drawIds;\s*this\.syncSelectionClassCodes\(\);/);
  assert.match(sliceBetween('handleSelectionChange(event) {', 'mergeSelectedFeatures() {'), /this\.setSelectedDrawIds\(/);
  // 批量改类后同步刷新，解除置灰
  assert.match(
    sliceBetween('selectClass(classCode) {', 'handleDrawCreate(event) {'),
    /this\.syncSelectionClassCodes\(\);\s*this\.recordMutation\('properties'\);/,
  );
  // 地类读取逐个 try/catch（要素可能已被删除）
  const sync = sliceBetween('syncSelectionClassCodes() {', 'async discardAllEdits() {');
  assert.match(sync, /try \{\s*feature = this\.draw\?\.get\(drawId\) \|\| null;\s*\} catch \(_\) \{/);
  assert.match(sync, /feature\?\.properties\?\.class_code/);

  // 运行时守卫保留；union 结果的结构校验发生在替换画布之前
  const merge = sliceBetween('mergeSelectedFeatures() {', 'simplifySelectedFeatures() {');
  assert.match(merge, /if \(classCodes\.size > 1\) \{/, '异类运行时守卫不得移除');
  const structure = merge.indexOf('polygonStructureProblem(geometry)');
  assert.ok(structure > 0 && structure < merge.indexOf('this.draw.add('), '结构校验必须先于 draw.add');
  assert.match(merge, /合并失败：几何无效或边界过于复杂，可手动调整后重试/);
});

test('simplify validates geometry and skips faces that would become invalid (E2-c)', () => {
  assert.match(
    source,
    /import \{ polygonStructureProblem, simplifyPolygonSafely \} from '@\/utils\/classificationEditorGeometry\.mjs'/,
  );
  const body = sliceBetween('simplifySelectedFeatures() {', 'async saveDrawing()');
  const safe = body.indexOf('simplifyPolygonSafely(feature.geometry, {');
  assert.ok(safe > 0 && safe < body.indexOf('this.draw.add('), '带校验的简化必须先于替换画布');
  assert.match(body, /if \(!outcome\) \{\s*skippedCount \+= 1;\s*return;\s*\}/, '三次都不通过 → 跳过，原面不动');
  assert.match(body, /个面简化后会产生无效几何，已跳过，原面保持不变/);
  assert.doesNotMatch(body, /几何退化/, '旧的"无法简化（几何退化）"文案已替换');
  // 有面被跳过时不再同时提示"边缘已足够平滑"
  assert.match(body, /\} else if \(!skippedCount\) \{\s*\n\s*(?:\/\/[^\n]*\n\s*)*this\.\$message\.info\('选中面的边缘已足够平滑'\);/);
});

test('save 422 selects and locates the offending feature (E2-d)', () => {
  const save = sliceBetween('async saveDrawing()', 'async loadRevisions');
  const submittedIds = 'const submittedDrawIds = this.draw.getAll().features.map((feature) => feature.id);';
  assert.ok(save.indexOf(submittedIds) > 0 && save.indexOf(submittedIds) < save.indexOf('await saveClassificationRevision'), '必须在 await 之前记下提交快照各下标的画布 id');
  assert.match(save, /error\?\.status === 422\) \{\s*this\.locateSaveValidationFailure\(error, submittedDrawIds\);/);

  const locate = sliceBetween('locateSaveValidationFailure(error, submittedDrawIds) {', 'async downloadCurrent() {');
  assert.match(locate, /const featureIndex = error\?\.details\?\.feature_index;/);
  assert.match(locate, /error\?\.details\?\.reason \|\| error\?\.details\?\.message \|\| error\?\.message/, '原因回退链保持不变');
  assert.match(locate, /!Number\.isInteger\(featureIndex\) \|\| featureIndex < 0 \|\| featureIndex >= total/, '下标必须是合法整数');
  assert.match(locate, /this\.\$message\.error\(`无法保存：\$\{reason\}`\);/, '请求体级错误（无 feature_index）保持仅回显原因');
  assert.match(locate, /this\.draw\.changeMode\('simple_select', \{ featureIds: \[targetId\] \}\);/);
  assert.match(locate, /this\.setSelectedDrawIds\(\[targetId\]\);/, '本地选中态与画布同步');
  assert.match(locate, /\{ padding: 80, maxZoom: 18, duration: 400 \}/);
  assert.match(
    locate,
    /无法保存：第 \$\{featureIndex \+ 1\} 个面（共 \$\{total\} 个）\$\{reason\}\$\{located\}/,
    '提示给出 1 起的序号与总数',
  );
  assert.match(locate, /'，已在地图上选中并定位'/);
});

test('draft export downloads the unsaved canvas instead of the saved revision (E2-e)', () => {
  // 未修改态明确写"已保存"：此前的"导出当前 GeoJSON"里"当前"二字含糊，用户以为
  // 导出的是画布上看到的内容，实际是服务端的已保存版本（审计"导出文案"项）
  assert.match(source, /\{\{ isDirty \? '导出草稿 GeoJSON' : '导出已保存 GeoJSON' \}\}/, '按钮文案随脏状态切换，且不出现含糊的"当前"');
  const download = sliceBetween('async downloadCurrent() {', 'exportDraft(resultId) {');
  assert.match(download, /const seq = this\.reloadSeq;\s*const exportResultId = this\.context\.resultId;/, 'await 之前捕获会话代次');
  assert.match(download, /if \(this\.isDirty\) \{\s*this\.exportDraft\(exportResultId\);\s*return;\s*\}/);
  assert.match(download, /await exportClassificationResult\(this\.context\.projectId, exportResultId\)/, '无修改时保持服务端导出');
  assert.match(download, /\} catch \(error\) \{\s*if \(!stillOwnsState\(\)\) return;\s*this\.\$message\.error\(/, '错误提示要过归属检查');
  assert.match(
    download,
    /\} finally \{[^}]*if \(stillOwnsState\(\) \|\| this\.reloadSeq === -1\) \{\s*this\.exporting = false;/,
    'exporting 复位要过归属检查',
  );
  // 上下文被接管后 exporting 由 reloadResult 复位，否则新成果的导出按钮会一直转圈
  assert.match(sliceBetween('async reloadResult()', 'initializeMap() {'), /this\.saving = false;\s*this\.exporting = false;/);

  const draft = sliceBetween('exportDraft(resultId) {', 'classColor(definition) {');
  assert.match(draft, /buildEditableFeatureCollection\(this\.draw\.getAll\(\)\)/, '草稿与保存提交用同一套规范化');
  assert.match(draft, /new Blob\(\[JSON\.stringify\(draft\)\], \{ type: 'application\/geo\+json' \}\)/);
  assert.match(draft, /draftExportFilename\(resultId, this\.result\?\.current_revision_no\)/);
  assert.doesNotMatch(draft, /exportClassificationResult/, '草稿导出不请求服务端');
});
