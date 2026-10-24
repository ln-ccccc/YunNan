import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

// 源码形状守护（沿 classificationEditorLifecycle 惯例）：loading 计数对称性。
// 请求拦截器对 silent 请求不 show（不计数），响应分支却无条件 hide 会把并发
// 可见请求的计数提前扣掉——全屏加载在仍在途的请求期间提前消失
// （2026-10-24 审查 P2-9 计数失衡）。
const source = fs.readFileSync(
  fileURLToPath(new URL('../src/api/request.js', import.meta.url)),
  'utf8',
);

test('silent requests never decrement the visible loading counter', () => {
  // show 侧：silent 请求不计数
  assert.ok(
    source.includes('if (!reqConfig.silent) {\n      showFullScreenLoading();'),
    '请求拦截器的 show 必须由 !silent 门控',
  );

  // hide 侧：两个响应分支都必须对称门控，且不存在裸调用
  const guarded = source.split('if (!silent) hideFullScreenLoading();').length - 1;
  const totalCalls = source.split('hideFullScreenLoading();').length - 1;
  assert.equal(guarded, 2, '成功/失败两个响应分支都必须 if (!silent) hide');
  assert.equal(totalCalls, 2, '不得存在未门控的 hideFullScreenLoading 调用');
});
