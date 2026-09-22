import assert from 'node:assert/strict';
import test from 'node:test';

import {
  CLASS_COLORS,
  CLASS_COLOR_FALLBACK,
  classColor,
  classColorExpression,
} from '../src/utils/classificationColors.mjs';

test('classColor prefers the rgb array carried by the class definition', () => {
  assert.equal(classColor({ class_code: 1, rgb: [10, 20, 30] }), 'rgb(10,20,30)');
});

test('classColor falls back to the fixed class_code palette then the brand color', () => {
  assert.equal(classColor({ class_code: 2 }), '#ff0000');
  assert.equal(classColor({ class_code: 5 }), '#00bfff');
  assert.equal(classColor({ class_code: 99 }), CLASS_COLOR_FALLBACK);
  assert.equal(classColor({}), CLASS_COLOR_FALLBACK);
  assert.equal(classColor(null), CLASS_COLOR_FALLBACK);
  // rgb 数组残缺（长度不足）不得产生 'rgb(1,2,undefined)'
  assert.equal(classColor({ class_code: 0, rgb: [1, 2] }), '#00ff00');
  // 元素非有限数字同样回退，不得产出非法 CSS 'rgb(1,2,x)' 让色块静默透明
  assert.equal(classColor({ class_code: 1, rgb: [1, 2, 'x'] }), '#008000');
  assert.equal(classColor({ class_code: 1, rgb: [1, 2, NaN] }), '#008000');
  assert.equal(classColor({ class_code: 1, rgb: [1, 2, null] }), '#008000');
});

test('classColorExpression maps every palette code onto the paint match expression', () => {
  const expr = classColorExpression();
  assert.equal(expr[0], 'match');
  assert.equal(expr[1][1], 'user_class_code');
  // 每个 class_code 的颜色必须出现在表达式中（match 的 label/value 对）
  for (const [code, color] of Object.entries(CLASS_COLORS)) {
    const codeIndex = expr.indexOf(Number(code));
    assert.ok(codeIndex > 0, `code ${code} 应出现在 match 表达式中`);
    assert.equal(expr[codeIndex + 1], color);
  }
  // 兜底色必须是最后一项，未知 code 不裸奔
  assert.equal(expr[expr.length - 1], CLASS_COLOR_FALLBACK);
});
