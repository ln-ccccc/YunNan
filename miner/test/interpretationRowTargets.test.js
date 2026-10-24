import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const source = fs.readFileSync(new URL('../src/components/SmartInterpretationView.vue', import.meta.url), 'utf8');

// fid+year 映射键守护（2026-10-24 审查 P2-7）：同年两任务两成果曾合并成一个
// result_id，两行"编辑矢量/智能复核"按钮都指向同一条（另一条的修订入口丢失）。
test('解译记录行优先用自身 result_id，fid:year 映射仅作回退', () => {
  const resolver = source.slice(source.indexOf('const resolveRowTarget'), source.indexOf('const openEditor'));
  assert.ok(resolver.includes('row.data?.result_id'), '行内 result_id 必须优先');
  assert.ok(resolver.includes('editorTargets.value.get'), '缺行内 id 时回退溯源映射');
  // 回退口径与后端 mine_traceability 一致：id 倒序首个非 vector_failed
  const build = source.slice(source.indexOf('for (const item of Array.isArray(items)'), source.indexOf('editorTargets.value = map;'));
  assert.ok(
    build.includes("existing.vectorStatus === 'vector_failed' && item.vector_status !== 'vector_failed'"),
    '矢量化失败的最新行不得抹掉同年既有好成果',
  );

  // 消费端统一走 resolver：两处按钮 + openEditor/openReview 不得再各自 map.get
  assert.equal(source.split('resolveRowTarget(row)?.editable').length - 1, 2, '两个按钮的 v-if 都走 resolver');
  const handlers = source.slice(source.indexOf('const openEditor'), source.indexOf('const openEditorTarget'));
  assert.ok(!handlers.includes('editorTargets.value.get'), 'openEditor/openReview 不得绕过 resolver 直接按键取值');
});
