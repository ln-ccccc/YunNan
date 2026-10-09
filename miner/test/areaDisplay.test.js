import assert from 'node:assert/strict';
import test from 'node:test';
import { formatAreaKm2 } from '../src/utils/areaDisplay.js';

test('面积从平方米换算为平方千米，不能只替换单位标签', () => {
  assert.equal(formatAreaKm2(245000), '0.245 km²');
  assert.equal(formatAreaKm2('1000000'), '1.000 km²');
  assert.equal(formatAreaKm2(12750000), '12.750 km²');
});

test('零面积与很小的非零面积可以区分', () => {
  assert.equal(formatAreaKm2(0), '0.000 km²');
  assert.equal(formatAreaKm2(1000), '0.001 km²');
  assert.equal(formatAreaKm2(1), '< 0.001 km²');
});

test('缺失或无效面积不伪装成零', () => {
  for (const value of [null, undefined, '', 'invalid', NaN, Infinity, -1]) {
    assert.equal(formatAreaKm2(value), '—');
  }
});
