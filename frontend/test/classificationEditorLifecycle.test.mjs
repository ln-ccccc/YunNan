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
// 编辑期几何接口的客户端（2026-11-24）：合法性、简化、合并都在后端几何引擎里做
const apiSource = fs.readFileSync(
  fileURLToPath(new URL('../src/api/classificationResults.js', import.meta.url)),
  'utf8',
);

// 把注释替换成等长空白：注释里的示例写法不应被当成调用
function stripComments(text) {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, (block) => block.replace(/[^\n]/g, ' '))
    .replace(/(^|[^:'"`\\])\/\/[^\n]*/g, (line, lead) => lead + ' '.repeat(line.length - lead.length));
}

// 找出源码里所有 $message.<type>(…) 调用，arguments 为括号内的源码。括号/字符串按字面配对，
// 忽略注释与字符串内部的分隔符。
function messageCalls(text) {
  const code = stripComments(text);
  const calls = [];
  const pattern = /\$message\.(success|warning|info|error)\(/g;
  let match = pattern.exec(code);
  while (match) {
    const start = match.index + match[0].length;
    let depth = 1;
    let index = start;
    let quote = null;
    while (index < code.length && depth > 0) {
      const char = code[index];
      if (quote) {
        if (char === '\\') index += 1;
        else if (char === quote) quote = null;
      } else if (char === '"' || char === "'" || char === '`') {
        quote = char;
      } else if (char === '(' || char === '[' || char === '{') {
        depth += 1;
      } else if (char === ')' || char === ']' || char === '}') {
        depth -= 1;
      }
      index += 1;
    }
    calls.push({ signature: match[0].slice(0, -1), arguments: code.slice(start, index - 1) });
    pattern.lastIndex = index - 1;
    match = pattern.exec(code);
  }
  return calls;
}

// 按顶层逗号拆分实参
function topLevelArguments(argsSource) {
  const args = [];
  let depth = 0;
  let quote = null;
  let start = 0;
  for (let index = 0; index < argsSource.length; index += 1) {
    const char = argsSource[index];
    if (quote) {
      if (char === '\\') index += 1;
      else if (char === quote) quote = null;
      continue;
    }
    if (char === '"' || char === "'" || char === '`') {
      quote = char;
    } else if (char === '(' || char === '[' || char === '{') {
      depth += 1;
    } else if (char === ')' || char === ']' || char === '}') {
      depth -= 1;
    } else if (char === ',' && depth === 0) {
      args.push(argsSource.slice(start, index).trim());
      start = index + 1;
    }
  }
  const tail = argsSource.slice(start).trim();
  if (tail) args.push(tail);
  return args;
}

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
  // 底图覆盖范围同样交给 source（2026-11-24）：视野超出覆盖的部分不再逐格请求不存在的瓦片
  // （一轮真实编辑测试 192 条 404）。bounds 必须先过 validBounds；清单没有范围时不加限制
  assert.match(sourceBuild, /\.\.\.\(bounds \? \{ bounds \} : \{\}\)/, 'raster source 必须带上底图覆盖范围');
  assert.match(source, /const bounds = validBounds\(mapManifest\.bounds\);\s*const center = bounds/, '传给 source 的 bounds 必须是校验过的清单范围');
});

// ─── 2026-11-23 缺陷修复的源码形状守护 ────────────────────────────────────────
// 以上既有守护在历次改造后原样通过，未做任何调整。
// 组件依赖 maplibre-gl / DOM，无法在 node:test 里实例化，组件侧沿用"源码形状守护"；
// 可脱离 DOM 的判定逻辑都在 utils/*.mjs 里，行为断言见对应的 *.test.mjs。
// 2026-11-24 的改造（合法性 / 简化 / 合并交给后端几何引擎）改写了下面三条与"前端自行
// 判定几何"有关的守护——E2-b 里的结构校验、E2-c、ROI 容差——断言标准不降；新增项在文件末尾。

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
  // 拦截文案由纯函数生成，连按不堆叠。选项必须放进第一个对象参数：Element Plus 2.1.10 的
  // $message.warning(text, X) 第二个参数是 appContext——曾写成 (text, { grouping: true })，
  // 真实浏览器里消息组件渲染时抛 "Object prototype may only be an Object or null"，
  // 面保住了但提示弹不出来（2026-11-24 浏览器验收抓到；旧断言反而把这个写法锁成了"正确"）
  assert.match(
    source,
    /this\.\$message\.warning\(\{\s*message: describeVertexDeletionBlockers\(blockers, geometry\),\s*grouping: true,\s*\}\)/,
    '分组提示的选项必须作为 message 对象的字段传入',
  );
  // 全组件通检：任何 $message.<type>(...) 调用都只能有一个顶层参数
  for (const [index, call] of messageCalls(source).entries()) {
    assert.equal(
      topLevelArguments(call.arguments).length,
      1,
      `第 ${index + 1} 处 ${call.signature} 必须只有一个顶层参数（选项放进第一个对象，第二个参数是 appContext）`,
    );
  }
  // 旧的"数当前顶点数"函数及其容器捕获拦截已删除
  assert.doesNotMatch(source, /polygonVertexCount/, 'polygonVertexCount 已被 vertexDeletionBlockers 取代');
  // 文件里更前面还有撤销/重做用的 document keydown，切片锚点必须用这段代码独有的名字
  const clickGuard = source.slice(source.indexOf("const guideTrashWithoutVertex"), source.indexOf("const TRASH_KEYS"));
  assert.match(clickGuard, /if \(!hasSelectedVertex\) \{\s*\n\s*this\.\$message\.info\(/, '无选中顶点的引导提示保留');
  assert.doesNotMatch(
    clickGuard,
    /stopPropagation|preventDefault/,
    '容器捕获监听只负责引导提示，不再在点击路上拦截删除',
  );
  assert.match(
    clickGuard,
    /e\.target\.closest\('\.mapbox-gl-draw_trash'\)\) return;\s*guideTrashWithoutVertex\(\);\s*\},\s*true,/,
    '删除按钮的引导仍挂在容器捕获阶段',
  );
  // 画布上的按键在容器 keydown 里补齐：mapbox-gl-draw 自己的键盘处理只认 class 含 mapboxgl-canvas
  // 的目标，MapLibre 的画布是 maplibregl-canvas，Esc 与 Delete/Backspace 原本在画布上都没有效果。
  // Delete/Backspace 必须走 draw.trash()（与工具栏按钮汇合，顶点删除防护对按键同样生效），
  // 且只认以画布为目标的事件。2026-11-24 在真实成果上测出此前按键路径不通，而旧的浏览器回归
  // 被上一步按钮提示的残留满足，属于假阳性。
  const keyGuard = source.slice(source.indexOf("const TRASH_KEYS"), source.indexOf("this.map.addSource('result-roi'"));
  assert.match(
    keyGuard,
    /if \(e\.key === 'Escape' && this\.draw\.getMode\(\) === 'direct_select'\) \{[\s\S]{0,260}this\.draw\.changeMode\('simple_select'/,
    'Esc 退出顶点模式保留',
  );
  assert.match(source, /const TRASH_KEYS = new Set\(\['Delete', 'Backspace'\]\)/, '删除键固定为 Delete 与 Backspace');
  assert.match(
    keyGuard,
    /if \(!TRASH_KEYS\.has\(e\.key\) \|\| !\(e\.target instanceof HTMLCanvasElement\)\) return;\s*e\.preventDefault\(\);\s*guideTrashWithoutVertex\(\);\s*this\.draw\.trash\(\);/,
    'Delete/Backspace 只在画布上生效，并经 draw.trash() 与删除按钮汇合',
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
  // 图斑与 ROI 用 64/20，manifest 保持 64/15。上限 20 级是 2026-11-24 在真实数据上改的：封顶 16 级时
  // 60 ~ 320 米的生产矿山在画布上只占几十个像素
  assert.match(
    fit,
    /contentBounds \? \{ padding: 64, maxZoom: 20, duration: 0 \} : \{ padding: 64, maxZoom: 15, duration: 0 \}/,
    '图斑与 ROI 用 64/20，manifest 保持 64/15',
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
  // 2026-11-24 起合并是网络请求：在途期间按钮转圈并禁用
  assert.match(
    source,
    /<el-button\s*:loading="merging"\s*:disabled="selectedDrawIds\.length < 2 \|\| mixedClassSelection \|\| saving \|\| merging"\s*:title="mixedClassSelection \? '仅支持合并同地类的面' : '[^']*'"\s*plain\s*@click="mergeSelectedFeatures"\s*>\s*合并选中\s*<\/el-button>/,
  );
  assert.match(source, /mixedClassSelection\(\) \{\s*return this\.selectedClassCodes\.length > 1;/);
  // selectedDrawIds 只有一个写入口，写入口同步刷新地类集合（不会出现过期地类）
  const writers = [...source.matchAll(/this\.selectedDrawIds = /g)];
  assert.equal(writers.length, 1, 'this.selectedDrawIds 只能在 setSelectedDrawIds 里赋值');
  const writer = sliceBetween('setSelectedDrawIds(drawIds) {', 'setSelectedDrawIds([]);');
  assert.match(writer, /this\.selectedDrawIds = drawIds;\s*this\.syncSelectionClassCodes\(\);/);
  assert.match(sliceBetween('handleSelectionChange(event) {', 'async mergeSelectedFeatures() {'), /this\.setSelectedDrawIds\(/);
  // 批量改类后同步刷新，解除置灰
  assert.match(
    sliceBetween('selectClass(classCode) {', 'handleDrawCreate(event) {'),
    /this\.syncSelectionClassCodes\(\);\s*this\.recordMutation\('properties'\);/,
  );
  // 地类读取逐个 try/catch（要素可能已被删除）
  const sync = sliceBetween('syncSelectionClassCodes() {', 'async discardAllEdits() {');
  assert.match(sync, /try \{\s*feature = this\.draw\?\.get\(drawId\) \|\| null;\s*\} catch \(_\) \{/);
  assert.match(sync, /feature\?\.properties\?\.class_code/);

  // 运行时守卫保留。合并结果的结构 / 有效性 / 是否越界不再由前端判定：原先这里是对 turf union
  // 的结果做 polygonStructureProblem 结构校验，现在合并请求的返回值一定通过了保存同款校验，
  // 画布上只套用后端返回的几何
  const merge = sliceBetween('async mergeSelectedFeatures() {', 'async simplifySelectedFeatures() {');
  assert.match(merge, /if \(classCodes\.size > 1\) \{/, '异类运行时守卫不得移除');
  const mixedGuard = merge.indexOf('if (classCodes.size > 1) {');
  const unionCall = merge.indexOf('await unionClassificationGeometries(');
  const drawAdd = merge.indexOf('this.draw.add(');
  assert.ok(mixedGuard > 0 && mixedGuard < unionCall, '同地类守卫必须先于发出合并请求');
  assert.ok(unionCall > 0 && unionCall < drawAdd, '合并结果必须来自后端请求，且请求先于替换画布');
  assert.match(merge, /geometry: merged\.geometry,/, '画布上套用的必须是后端返回的几何，前端不得再加工或校验');
  assert.doesNotMatch(withoutComments(merge), /polygonStructureProblem|polygonGeometryProblems/, '结构校验已由后端几何引擎承担');
  // 失败文案：有具体原因用后端的，没有就沿用原来这句
  assert.match(merge, /合并失败：\$\{requestFailureReason\(error, MERGE_FAILURE_FALLBACK\)\}/);
  assert.match(source, /const MERGE_FAILURE_FALLBACK = '几何无效或边界过于复杂，可手动调整后重试';/);
  assert.match(
    source,
    /function requestFailureReason\(error, fallback\) \{\s*return error\?\.details\?\.reason \|\| error\?\.message \|\| fallback;\s*\}/,
    '原因优先级：后端 details.reason → 拦截器 message → 兜底文案',
  );
});

test('simplify takes every verdict from the backend and leaves rejected faces untouched (E2-c)', () => {
  // 前端不再自带简化、有效性与越界判定（原 simplifyPolygonSafely + turf simplify + roiViolation）：
  // 简化与「简化后是否仍合法」都在后端几何引擎里完成，changed 的结果一定通过了保存同款校验
  const code = withoutComments(sliceBetween('async simplifySelectedFeatures() {', 'async saveDrawing()'));
  const simplifyCall = code.indexOf('await simplifyClassificationGeometries(');
  assert.ok(simplifyCall > 0 && simplifyCall < code.indexOf('this.draw.add('), '简化结果必须来自后端请求，且请求先于替换画布');
  assert.doesNotMatch(code, /tolerance/, '容差用后端默认，前端不得传 tolerance');
  // 只有 changed 才替换画布；rejected（简化后会越界或几何无效）计入跳过、原面不动；unchanged 不动
  assert.match(
    code,
    /if \(!target \|\| item\?\.outcome !== 'changed'\) \{\s*if \(item\?\.outcome === 'rejected'\) skippedCount \+= 1;\s*return;\s*\}/,
  );
  assert.match(code, /geometry: item\.geometry,/, '替换画布的几何逐项取自后端返回');
  assert.match(code, /properties: live\.properties,/, '原面的属性原样保留');
  assert.doesNotMatch(code, /roi_geometry/, '越界与否由后端判定，前端不得再拿 ROI 做几何运算');
  assert.match(code, /个面简化后会超出矿山边界或几何无效，已跳过，原面保持不变/);
  assert.doesNotMatch(code, /几何退化/, '旧的"无法简化（几何退化）"文案已替换');
  // 有面被跳过、或在请求期间被改动时，不再同时提示"边缘已足够平滑"
  assert.match(
    code,
    /\} else if \(!skippedCount && !changedMeanwhileCount\) \{\s*this\.\$message\.info\('选中面的边缘已足够平滑'\);/,
  );
  assert.match(code, /if \(!live\) \{\s*changedMeanwhileCount \+= 1;\s*return;\s*\}/, '请求期间被改动的面不套用简化结果，单独计数');
  // 只有真的改动画布才清选中、记历史
  assert.match(code, /if \(simplifiedCount\) \{\s*this\.setSelectedDrawIds\(\[\]\);\s*this\.recordMutation\('simplify'\);/);
  // 逐面「先 add 后 delete」并各自包在 try/catch 里：单面失败只跳过这一面，不中断整批
  assert.match(
    code,
    /try \{\s*this\.draw\.add\(\{\s*type: 'Feature',\s*id: createClientFeatureId\(\),\s*properties: live\.properties,\s*geometry: item\.geometry,\s*\}\);\s*this\.draw\.delete\(target\.id\);\s*simplifiedCount \+= 1;\s*\} catch \(_\) \{\s*skippedCount \+= 1;\s*\}/,
  );
  assert.match(source, /title="轻度简化选中面边缘（≈1m 容差）"/, '按钮说明不变');
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
  // （合并 / 简化从同步计算变成网络请求后，2026-11-24，它们的标志一并在这里复位）
  assert.match(
    withoutComments(sliceBetween('async reloadResult()', 'initializeMap() {')),
    /this\.saving = false;\s*this\.exporting = false;\s*this\.merging = false;\s*this\.simplifying = false;/,
  );

  const draft = sliceBetween('exportDraft(resultId) {', 'classColor(definition) {');
  assert.match(draft, /buildEditableFeatureCollection\(this\.draw\.getAll\(\)\)/, '草稿与保存提交用同一套规范化');
  assert.match(draft, /new Blob\(\[JSON\.stringify\(draft\)\], \{ type: 'application\/geo\+json' \}\)/);
  assert.match(draft, /draftExportFilename\(resultId, this\.result\?\.current_revision_no\)/);
  assert.doesNotMatch(draft, /exportClassificationResult/, '草稿导出不请求服务端');
});

// ─── 2026-11-24：绘制进行中不得与"整幅画布"操作冲突 ───────────────────────────
// 画到一半的草稿是一个没有地类、环也不闭合的要素，而且只在绘制模式的状态里被引用。
// 通读 mapbox-gl-draw 1.5.1 的实现后确认了三处冲突（真实成果上均未被发现，因为
// 以前的测试都是画完才操作）：未完成就点保存 → 报"class_code 必须为 0 到 5 的整数"；
// Ctrl+Z / 重做 / 放弃全部修改 → draw.set() 把草稿删出库而绘制模式仍在引用它，之后
// 点击没有反应、回车时草稿凭空消失；Delete/Backspace（12b79de 让它生效后）直接丢整个草稿。

test('a sketch in progress never meets whole-canvas operations (2026-11-24)', () => {
  // 绘制模式的"删除"改为退回上一个顶点；模式变化同步到响应式状态
  assert.match(
    sliceBetween('modes: {', 'this.map.addControl(this.draw'),
    /draw_polygon: createStepBackDrawPolygon\(MapboxDraw\.modes\.draw_polygon\)/,
    '绘制模式必须换成"退回上一个顶点"的工厂',
  );
  assert.match(
    source,
    /this\.map\.on\('draw\.modechange', \(event\) => \{ this\.drawMode = event\.mode; \}\);/,
    '模式变化要同步到 drawMode',
  );
  assert.match(source, /:disabled="\(!canUndo && !sketching\) \|\| saving"/, '绘制中撤销按钮保持可用（退回顶点）');

  // 撤销：绘制中只经 draw.trash() 退回顶点，绝不 draw.set()
  const undo = withoutComments(sliceBetween('undoLastEdit() {', 'redoLastEdit() {'));
  assert.match(
    undo,
    /if \(!this\.draw\) return;\s*if \(this\.draw\.getMode\(\) === SKETCH_MODE\) \{\s*this\.draw\.trash\(\);\s*return;\s*\}\s*if \(!this\.history\?\.canUndo\) return;[\s\S]*this\.draw\.set\(JSON\.parse\(previous\)\);/,
    '绘制中的撤销不得走到 draw.set',
  );
  const redo = withoutComments(sliceBetween('redoLastEdit() {', 'handleKeydown(event) {'));
  assert.match(redo, /if \(!this\.draw \|\| this\.draw\.getMode\(\) === SKETCH_MODE \|\| !this\.history\?\.canRedo\) return;/, '绘制中的重做不动作');

  // 保存与导出草稿：读整幅画布之前先拦截
  const save = withoutComments(sliceBetween('async saveDrawing() {', 'async loadRevisions'));
  assert.ok(
    /if \(!this\.context \|\| !this\.result \|\| !this\.draw\) return;\s*if \(this\.isSketching\(\)\) \{\s*this\.warnSketchInProgress\(\);\s*return;\s*\}\s*let featureCollection;\s*try \{\s*featureCollection = buildEditableFeatureCollection\(this\.draw\.getAll\(\)\)/.test(save),
    '保存必须在读画布之前拦截未完成的绘制',
  );
  const draft = withoutComments(sliceBetween('exportDraft(resultId) {', 'classColor(definition) {'));
  assert.ok(
    /exportDraft\(resultId\) \{\s*if \(this\.isSketching\(\)\) \{\s*this\.warnSketchInProgress\(\);\s*return;\s*\}/.test(draft),
    '导出草稿必须在读画布之前拦截未完成的绘制',
  );
  const warning = sliceBetween('warnSketchInProgress() {', 'setSelectedDrawIds(drawIds) {');
  assert.match(warning, /回车或双击完成绘制，按 Esc 取消/, '拦截提示要说清怎么完成、怎么取消');
  assert.match(
    sliceBetween('isSketching() {', 'warnSketchInProgress() {'),
    /return Boolean\(this\.draw\) && this\.draw\.getMode\(\) === SKETCH_MODE;/,
    '绘制中的判定以库的当前模式为准',
  );

  // 放弃全部修改 / 重置画布：先结束绘制模式再 draw.set
  const reset = withoutComments(sliceBetween('resetDrawing() {', 'setSelectedDrawIds(drawIds) {'));
  const exitSketch = reset.indexOf('if (this.draw.getMode() === SKETCH_MODE) {');
  const set = reset.indexOf('this.draw.set(');
  assert.ok(exitSketch > 0 && exitSketch < set, '重置画布前必须先结束绘制模式');
  assert.match(
    reset,
    /this\.suppressDrawEvents = true;\s*try \{\s*this\.draw\.changeMode\('simple_select', \{\}\);\s*\} finally \{\s*this\.suppressDrawEvents = false;\s*\}/,
    '结束草稿不得弹提示、不得记历史',
  );
  assert.match(source, /destroyMap\(\) \{[\s\S]*?this\.drawMode = 'simple_select';/, '地图销毁时复位模式');

  // 保存 422 的定位：保存期间用户可能又开始画面，此时切换模式会结束或丢弃草稿
  const locate = withoutComments(sliceBetween('locateSaveValidationFailure(error, submittedDrawIds) {', 'async downloadCurrent() {'));
  assert.match(
    locate,
    /const sketching = this\.isSketching\(\);\s*if \(target && !sketching\) \{\s*this\.draw\.changeMode\('simple_select'/,
    '绘制中不切换模式',
  );
  assert.match(locate, /target && !sketching \? '，已在地图上选中并定位' : ''/, '没有定位就不能说"已定位"');

  // Esc：普通选择下清除选中（上游 onSetup 直接读 opts.featureIds，必须传空对象）。
  // 已处于 simple_select 时 api.changeMode 只静默改选中、不发 draw.selectionchange，
  // 所以必须显式同步本地选中态——否则高亮已消失、工具栏仍显示"已选择"（夹具回归抓到）
  const keyGuard = source.slice(source.indexOf("const TRASH_KEYS"), source.indexOf("this.map.addSource('result-roi'"));
  assert.match(
    withoutComments(keyGuard),
    /if \(e\.key === 'Escape' && this\.draw\.getMode\(\) === 'simple_select' && this\.selectedDrawIds\.length\) \{\s*this\.draw\.changeMode\('simple_select', \{\}\);\s*this\.setSelectedDrawIds\(this\.draw\.getSelectedIds\(\)\);\s*return;\s*\}/,
    'Esc 清除选中后必须用库的当前选中同步本地状态',
  );

  // 面板里要写明键盘操作（此前的说明一个键都没提）
  const hints = sliceBetween('class="key-hints"', '</ul>');
  for (const key of ['回车', 'Esc', 'Backspace', 'Delete', 'Ctrl']) assert.ok(hints.includes(`<kbd>${key}</kbd>`), `键盘说明应包含 ${key}`);
});

test('superseded results are called out without breaking the vector_failed v-if chain (2026-11-24)', () => {
  const coverage = source.indexOf('v-if="coverageNotice"');
  const superseded = source.indexOf('v-if="result.effective === false"');
  const failed = source.indexOf("v-if=\"result.vector_status === 'vector_failed'\"");
  const toolbar = source.indexOf('v-else class="editor-toolbar"');
  assert.ok(coverage > 0 && coverage < superseded, '被取代提示排在覆盖提示之后');
  assert.ok(superseded < failed && failed < toolbar, '被取代提示自带 v-if，必须排在 vector_failed 的 v-if 之前，否则工具栏的 v-else 会改绑');
  const alert = sliceBetween('v-if="result.effective === false"', "v-if=\"result.vector_status === 'vector_failed'\"");
  assert.match(alert, /title="该成果已被同矿山同年更新的成果取代"/);
  assert.match(alert, /不会体现在概览、台账、变化矩阵与溯源中/, '要说清修订会去哪里、不会去哪里');
  // 只提示、不禁用：契约允许保存任意 ready / ready_empty 成果；旧后端没有该字段时不提示
  assert.doesNotMatch(source, /effective[\s\S]{0,80}:disabled/);
});

test('click targets are resolved by the corrected rule in both selection modes (2026-11-24)', () => {
  // mapbox-gl-draw 1.5.1 的"面按面积从小到大命中"从未生效：sort_features 给面积函数传的是
  // Feature，@mapbox/geojson-area 0.2.2 返回 undefined，比较结果恒为 NaN。实测新画的小面叠在
  // 大面上，点小面选中的是大面；真实成果默认视野下 57 个图斑只有 31 个能单独点中。
  const modes = sliceBetween('modes: {', 'this.map.addControl(this.draw');
  assert.match(modes, /simple_select: createGuardedSimpleSelect\(MapboxDraw\.modes\.simple_select\)/, '普通选择模式必须换成命中规则修正后的工厂');
  assert.match(
    modes,
    /\.\.\.MapboxDraw\.modes,\s*direct_select: createGuardedDirectSelect\(\s*MapboxDraw\.modes\.direct_select,/,
    '其余模式仍原样保留，顶点模式用带防护的工厂',
  );
});

// ─── 2026-11-24：合法性、简化、合并全部交给后端几何引擎 ──────────────────────
// 前端此前自带三套几何判定（turf booleanWithin 无容差判定、采样式 roiViolation、手写的有效性
// 镜像），与后端 Shapely/GEOS 的结论对不上：真实数据上 33% 的图斑被误报越界，简化后的面要到
// 保存才被 422 拒绝。现在保存、编辑期校验、简化、合并共用后端同一个判定函数，前端只做渲染、
// 交互与撤销重做。下面守护两件事：前端不再自行判定几何；每个结论都来自后端。
// 调度器本身的行为（合并请求、过期丢弃、cancel、失败静默）见 classificationEditorValidation.test.mjs。

test('the editor no longer judges geometry itself (2026-11-24)', () => {
  const code = withoutComments(source);
  assert.doesNotMatch(code, /@turf\//, '不得再引入任何 turf 几何库');
  assert.doesNotMatch(code, /classificationEditorGeometry/, '本地几何模块已被后端引擎取代并删除');
  for (const name of [
    'roiContainsGeometry', 'warnFeaturesOutsideRoi', 'roiViolation', 'restoreBoundaryVertices',
    'simplifyPolygonSafely', 'booleanWithin', 'polygonStructureProblem', 'polygonGeometryProblems',
  ]) {
    assert.doesNotMatch(code, new RegExp(name), `${name} 已被后端几何引擎取代`);
  }
  // 逐值相等比较只用来确认「请求期间这个面没被改过」，不是几何算法
  const compare = withoutComments(sliceBetween('function sameGeometry(left, right) {', 'function requestFailureReason('));
  assert.match(compare, /JSON\.stringify\(left\.coordinates\) === JSON\.stringify\(right\.coordinates\)/);
  assert.doesNotMatch(compare, /Math\.|orient|segment|intersects|within|contains/i, '不得借比较之名重写几何运算');

  // 三个接口都经同一个客户端发出
  assert.match(
    source,
    /import \{[^}]*simplifyClassificationGeometries,[^}]*unionClassificationGeometries,[^}]*validateClassificationGeometries,[^}]*\} from '@\/api\/classificationResults';/,
    '编辑器必须引入后端的校验 / 简化 / 合并接口',
  );
});

test('draw.create / draw.update queue the edited faces for backend validation (2026-11-24)', () => {
  const handlers = withoutComments(sliceBetween('handleDrawCreate(event) {', 'queueGeometryValidation(features, action) {'));
  // create：地类 / ID 赋值、suppressDrawEvents 守卫、历史记录全部保留，只是把本地判定换成排队送审
  assert.match(
    handlers,
    /handleDrawCreate\(event\) \{\s*if \(this\.suppressDrawEvents\) return;\s*event\.features\.forEach\(\(feature\) => \{\s*this\.draw\.setFeatureProperty\(feature\.id, 'class_code', this\.activeClassCode\);\s*this\.draw\.setFeatureProperty\(feature\.id, 'feature_id', createClientFeatureId\(\)\);\s*\}\);\s*this\.recordMutation\('create'\);\s*this\.queueGeometryValidation\(event\.features, VALIDATION_ACTION_CREATE\);/,
  );
  assert.match(
    handlers,
    /handleDrawUpdate\(event\) \{\s*if \(this\.suppressDrawEvents\) return;\s*this\.recordMutation\('vertex'\);\s*this\.queueGeometryValidation\(event\?\.features, VALIDATION_ACTION_UPDATE\);/,
  );
  assert.doesNotMatch(handlers, /\$message|roi_geometry/, '事件处理里不弹提示、不读 ROI：越界与否要等后端的结论');

  const queue = withoutComments(sliceBetween('queueGeometryValidation(features, action) {', 'async validateDrawGeometries(drawIds) {'));
  assert.match(queue, /this\.validationScheduler\.request\(drawIds, action\);/, '只把 draw id 记入待校验集合');

  // 防抖 200ms；取几何发请求与中文提示由组件注入。mapbox-gl-draw 的 draw.update 只在鼠标释放、
  // 增删顶点时各发一次，不需要再挂鼠标事件
  assert.match(source, /import \{ createValidationScheduler, matchVerdicts \} from '@\/utils\/classificationEditorValidation\.mjs';/);
  assert.match(source, /const VALIDATION_DEBOUNCE_MS = 200;/);
  assert.match(
    withoutComments(sliceBetween('created() {', 'mounted() {')),
    /delayMs: VALIDATION_DEBOUNCE_MS,\s*validate: \(drawIds\) => this\.validateDrawGeometries\(drawIds\),\s*onVerdicts: \(verdicts\) => this\.reportGeometryVerdicts\(verdicts\),/,
  );
  assert.doesNotMatch(withoutComments(sliceBetween('data() {', 'computed: {')), /validationScheduler/, '调度器不进 data()：它不需要响应式');
  assert.doesNotMatch(withoutComments(sliceBetween('this.map.on(\'load\'', 'destroyMap() {')), /mousedown|mouseup|mousemove/, '不得另加鼠标事件');
});

test('the validation request belongs to the editing session and fails silently (2026-11-24)', () => {
  const validate = withoutComments(sliceBetween('async validateDrawGeometries(drawIds) {', 'reportGeometryVerdicts('));
  const awaitAt = validate.indexOf('await validateClassificationGeometries(projectId, resultId, geometries)');
  assert.ok(awaitAt > 0, '应调用后端校验接口');
  // 发出之前捕获会话代次与成果（与 loadRevisions / saveDrawing 同构）
  for (const capture of ['const sessionSeq = this.reloadSeq;', 'const { projectId, resultId } = this.context || {};']) {
    const at = validate.indexOf(capture);
    assert.ok(at >= 0 && at < awaitAt, `必须在 await 之前捕获：${capture}`);
  }
  // 取不到几何的 id（期间已被删除）跳过；一个都不剩就不发请求
  const empty = validate.indexOf('if (!ids.length) return null;');
  assert.ok(empty > 0 && empty < awaitAt, '没有可校验的面时不发请求');
  assert.match(validate, /if \(!geometry\) return;/);
  assert.match(validate, /geometries\.push\(\{ type: geometry\.type, coordinates: geometry\.coordinates \}\);/, '请求体只带 type 与 coordinates');
  // 返回之后、交付之前四项比对；任一不符即没有结论
  const after = validate.slice(awaitAt);
  assert.match(
    after,
    /if \(\s*sessionSeq !== this\.reloadSeq\s*\|\| sessionSeq === -1\s*\|\| this\.context\?\.projectId !== projectId\s*\|\| this\.context\?\.resultId !== resultId\s*\) \{\s*return null;\s*\}/,
    '归属不符返回 null',
  );
  // 接口返回的是 { items } 对象，不是数组：必须经 matchVerdicts 对上号再交给调度器。
  // 2026-11-24 的第一版把整个响应对象当成数组往下传，调度器静默丢弃，界面上一条提示都不出，
  // 而当时的测试只数了「请求发出去了」——所以这里钉住对接方式，另由单测与浏览器夹具验证提示确实出现
  assert.match(
    validate,
    /const payload = await validateClassificationGeometries\(projectId, resultId, geometries\);/,
    '校验接口的返回值先落在 payload 上',
  );
  assert.match(
    after,
    /const items = matchVerdicts\(payload, ids\);\s*return items \? \{ ids, items \} : null;/,
    '结论数组必须与送审的面一一对应才交付，否则视为没有结论',
  );
  assert.doesNotMatch(after, /items: payload|items: await|\{ ids, items: payload \}/, '不得把响应对象当成结论数组');
  // 请求失败静默：不 catch、不提示——保存时后端仍会校验
  assert.doesNotMatch(after, /\$message|\bcatch\b/, '请求失败不得弹提示');
});

test('backend verdicts are reported in two categories with the original wording (2026-11-24)', () => {
  const report = withoutComments(sliceBetween('reportGeometryVerdicts({ items, contexts }) {', 'handleDrawDelete() {'));
  assert.match(report, /if \(!item \|\| item\.ok\) return;/, '合法的面不提示');
  assert.match(
    report,
    /if \(item\.code === 'OUTSIDE_ROI'\) \{\s*outsideActions\.push\(contexts\[index\]\);\s*\} else \{\s*invalidCount \+= 1;\s*\}/,
    '越界按「下一步」计数，其余不合法项并成一类',
  );
  assert.match(
    report,
    /if \(outsideActions\.length\) \{[\s\S]*this\.\$message\.warning\(`\$\{outsideActions\.length\} 个面超出成果 ROI（矿山边界），保存时将被拒绝\$\{hint\}`\);\s*\}\s*if \(invalidCount\) \{\s*this\.\$message\.warning\(`\$\{invalidCount\} 个面几何无效（如自相交），保存时将被拒绝，请调整顶点位置`\);/,
    '两类各有其提示，同一批里都有就各弹一条',
  );
  assert.match(
    report,
    /outsideActions\.every\(\(action\) => action === VALIDATION_ACTION_CREATE\);\s*const hint = allCreated \? '，请拖回界内或删除' : '，请调整顶点位置';/,
    '新建的面：拖回界内或删除；改过顶点的：调整顶点位置（与改造前的文案一致）',
  );
});

test('pending validations die with the canvas (2026-11-24)', () => {
  const destroy = withoutComments(sliceBetween('destroyMap() {', 'fitToCurrentFeatures(manifestBounds = null) {'));
  const cancel = destroy.indexOf('this.validationScheduler.cancel()');
  assert.ok(cancel > 0 && cancel < destroy.indexOf('this.map.remove()'), '销毁地图时先作废待校验集合与在途响应');
  // destroyMap 是 reloadResult 与 beforeUnmount 共用的清理点：切换成果、卸载组件都经它
  assert.match(sliceBetween('async reloadResult()', 'initializeMap() {'), /this\.reloadSeq = \(this\.reloadSeq \|\| 0\) \+ 1\);\s*this\.destroyMap\(\);/);
  assert.match(withoutComments(sliceBetween('beforeUnmount() {', 'async beforeRouteLeave(')), /this\.destroyMap\(\);\s*this\.reloadSeq = -1;/);
  // 放弃全部修改把画布重置回已保存的状态：对着被丢弃的编辑做的校验同样作废，不得事后补弹提示
  const reset = withoutComments(sliceBetween('resetDrawing() {', 'setSelectedDrawIds(drawIds) {'));
  const resetCancel = reset.indexOf('this.validationScheduler.cancel()');
  assert.ok(resetCancel > 0 && resetCancel < reset.indexOf('this.draw.set('), '重置画布前先作废待校验与在途的校验');
});

test('merge and simplify are backend requests guarded against re-entry and stale context (2026-11-24)', () => {
  const cases = [
    { method: 'mergeSelectedFeatures', flag: 'merging', request: 'await unionClassificationGeometries(', end: 'async simplifySelectedFeatures() {' },
    { method: 'simplifySelectedFeatures', flag: 'simplifying', request: 'await simplifyClassificationGeometries(', end: 'async saveDrawing()' },
  ];
  for (const { method, flag, request, end } of cases) {
    const code = withoutComments(sliceBetween(`async ${method}() {`, end));
    const requestAt = code.indexOf(request);
    assert.ok(requestAt > 0, `${method} 应请求后端`);
    // 重入：在途期间再次触发直接返回
    const entry = code.indexOf(`|| this.${flag}) return;`);
    assert.ok(entry > 0 && entry < requestAt, `${method} 必须先拦重入`);
    // 发出之前捕获会话代次与成果；返回后不是同一个成果就不动画布、不提示
    const seqCapture = code.indexOf('const seq = this.reloadSeq;');
    const resultCapture = code.slice(0, requestAt).search(/const \w+ = this\.context\?\.resultId;/);
    assert.ok(seqCapture > 0 && seqCapture < requestAt, `${method} 必须在 await 之前捕获 reloadSeq`);
    assert.ok(resultCapture >= 0, `${method} 必须在 await 之前捕获 resultId`);
    const after = code.slice(requestAt);
    const guard = after.indexOf('if (!stillOwnsState()) return;');
    assert.ok(guard >= 0 && guard < after.indexOf('this.draw.add('), `${method}：await 之后的画布更新前必须先过 stillOwnsState 守卫`);
    assert.match(code, /\} catch \(error\) \{\s*if \(!stillOwnsState\(\)\) return;\s*this\.\$message\.error\(/, `${method}：错误提示同样要过归属检查`);
    const resetGuard = new RegExp(String.raw`if \(stillOwnsState\(\) \|\| this\.reloadSeq === -1\) \{\s*this\.${flag} = false;\s*\}`);
    assert.match(code, resetGuard, `${method}：finally 复位标志前必须确认仍持有上下文`);
    // 先 add 后 delete 的原子化写法与 suppressDrawEvents 包裹保留：add 与 delete 都必须落在包裹之内
    const add = code.indexOf('this.draw.add(');
    const remove = code.indexOf('this.draw.delete(');
    const unsuppress = code.indexOf('this.suppressDrawEvents = false;', add);
    assert.ok(add > 0 && remove > add, `${method}：先 add 后 delete`);
    assert.ok(unsuppress > remove, `${method}：delete 也必须在 suppressDrawEvents 包裹之内`);
    assert.match(code, /this\.suppressDrawEvents = true;\s*try \{[\s\S]*this\.draw\.add\([\s\S]*\} finally \{\s*this\.suppressDrawEvents = false;\s*\}/, `${method}：替换画布全程包在 suppressDrawEvents 里`);
    // 请求期间画布没有冻结：被拖动、删除或被撤销 / 重做换掉的面不套用结果
    assert.match(code, /this\.unchangedLiveFeature\(/, `${method}：套用结果前必须确认那个面没被改过`);
    assert.match(source, new RegExp(String.raw`:loading="${flag}"\s*:disabled="[^"]*\|\| ${flag}"`), `${method}：请求期间按钮转圈并禁用`);
  }
  // 合并：同地类的前提对着画布现状再核一次，地类也取现状
  const merge = withoutComments(sliceBetween('async mergeSelectedFeatures() {', 'async simplifySelectedFeatures() {'));
  assert.match(merge, /const liveClassCodes = new Set\(live\.map\(\(feature\) => feature\.properties\?\.class_code\)\);\s*if \(liveClassCodes\.size > 1\) \{/);
  assert.match(merge, /const classCode = live\[0\]\.properties\?\.class_code \?\? this\.activeClassCode;/);
  assert.match(merge, /this\.recordMutation\('merge'\);\s*this\.\$message\.success\(`已合并为 1 个面（原 \$\{selected\.length\} 个）`\);/);
  // 切换成果时一并复位（在途旧请求的 finally 不清理新上下文的标志）
  assert.match(
    withoutComments(sliceBetween('async reloadResult()', 'initializeMap() {')),
    /this\.saving = false;\s*this\.exporting = false;\s*this\.merging = false;\s*this\.simplifying = false;/,
  );
});

test('the three geometry endpoints post bare geometries under the result URL', () => {
  for (const [name, suffix] of [
    ['validateClassificationGeometries', '/geometry/validate'],
    ['simplifyClassificationGeometries', '/geometry/simplify'],
    ['unionClassificationGeometries', '/geometry/union'],
  ]) {
    const start = apiSource.indexOf(`export async function ${name}(`);
    assert.ok(start > 0, `${name} 应存在`);
    const body = withoutComments(apiSource.slice(start, apiSource.indexOf('\n}', start) + 2));
    assert.match(body, new RegExp(`resultUrl\\(projectId, resultId, '${suffix}'\\)`), `${name} 必须挂在成果 URL 下`);
    assert.match(
      body,
      /classificationClient\.post\(\s*resultUrl\([^)]*\),\s*geometriesPayload\(geometries\),\s*\{\s*timeout:\s*\d+\s*\},?\s*\);\s*return response\.data\.data;/,
      `${name}：请求体只能是 geometries 数组，必须带超时（请求挂住不能让按钮一直转圈），返回 data`,
    );
    assert.doesNotMatch(body, /tolerance|base_revision_no|properties/, `${name}：请求里不得夹带容差、版本号或属性`);
  }
  const apiCode = withoutComments(apiSource);
  assert.doesNotMatch(apiCode, /tolerance/, '前端不得传 tolerance：容差由后端决定');
  assert.match(
    apiCode,
    /geometries\.map\(\(geometry\) => \(\{ type: geometry\?\.type, coordinates: geometry\?\.coordinates \}\)\)/,
    '每个几何只带 type 与 coordinates（后端多一个键就 422）',
  );
});
