import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildProgressText,
  formatBytes,
  formatDuration,
  pushProgressSample,
} from '../src/utils/uploadProgress.mjs';

test('pushProgressSample estimates speed over a sliding window', () => {
  let state = { samples: [], bytesPerSecond: null };
  state = pushProgressSample(state.samples, 0, 0);
  assert.equal(state.bytesPerSecond, null, '单样本无速率');
  state = pushProgressSample(state.samples, 1000, 1024 * 1024);
  assert.ok(Math.abs(state.bytesPerSecond - 1024 * 1024) < 1000, '1MiB/s');

  // 窗口滑动：注入 8 个高速点后，早期慢速点移出窗口
  for (let i = 2; i <= 12; i++) {
    state = pushProgressSample(state.samples, i * 1000, i * 10 * 1024 * 1024);
  }
  assert.ok(state.bytesPerSecond > 9 * 1024 * 1024, '后期速率主导');
  assert.ok(state.samples.length <= 8, '窗口裁剪');

  // 字节倒退/非法输入安全
  assert.equal(pushProgressSample(state.samples, 99999, 0).bytesPerSecond, null);
  assert.equal(pushProgressSample(state.samples, NaN, 5).bytesPerSecond, null);
});

test('formatBytes and formatDuration cover the human ladder', () => {
  assert.equal(formatBytes(0), '0B');
  assert.equal(formatBytes(2048), '2.0KB');
  assert.equal(formatBytes(3 * 1024 * 1024), '3.0MB');
  assert.equal(formatBytes(2.5 * 1024 * 1024 * 1024), '2.50GB');
  assert.equal(formatBytes(null), '—');
  assert.equal(formatBytes(NaN), '—');
  assert.equal(formatBytes(-5), '—');
  assert.equal(formatDuration(45), '45秒');
  assert.equal(formatDuration(195), '3分15秒');
  assert.equal(formatDuration(3700), '1时01分');
  assert.equal(formatDuration(-1), '—');
});

test('buildProgressText shows ETA only when speed is known', () => {
  const withSpeed = buildProgressText(2.4 * 1024 ** 3, 5.6 * 1024 ** 3, 10 * 1024 ** 2);
  assert.match(withSpeed, /^42% · 2\.4\d*GB\/5\.6\d*GB · 预计剩余 \d+分/);
  const noSpeed = buildProgressText(1024, 4096, null);
  assert.equal(noSpeed, '25% · 1.0KB/4.0KB');
  const noTotal = buildProgressText(1024, 0, null);
  assert.equal(noTotal, '0% · 1.0KB');
  // 封顶 99%（完成态由调用方置 100）
  assert.equal(buildProgressText(9999, 10000, 1).startsWith('99%'), true);
});
