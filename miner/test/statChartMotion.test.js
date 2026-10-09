import assert from 'node:assert/strict';
import test from 'node:test';
import { makeStatBarOption } from '../src/components/statBarOption.js';

test('统计图遵循减少动态效果偏好', () => {
  const previous = globalThis.matchMedia;
  try {
    globalThis.matchMedia = () => ({ matches: true });
    assert.equal(makeStatBarOption({ names: ['生态修复'], values: [2] }).animation, false);
    globalThis.matchMedia = () => ({ matches: false });
    assert.equal(makeStatBarOption({ names: ['生态修复'], values: [2] }).animation, true);
  } finally {
    if (previous === undefined) delete globalThis.matchMedia;
    else globalThis.matchMedia = previous;
  }
});
