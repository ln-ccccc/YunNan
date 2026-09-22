import assert from 'node:assert/strict';
import test from 'node:test';

import {
  MAX_UPLOAD_FILE_BYTES,
  MAX_UPLOAD_TOTAL_BYTES,
  checkUploadLimits,
  isDisconnectError,
} from '../src/utils/uploadGuards.mjs';

test('checkUploadLimits accepts files within both caps', () => {
  const result = checkUploadLimits([
    { name: 'a.tif', size: 1024 },
    { name: 'b.tif', size: 512 },
  ]);
  assert.equal(result.ok, true);
});

test('checkUploadLimits rejects a single file over 8GB without scanning the rest', () => {
  const result = checkUploadLimits([
    { name: 'a.tif', size: 1024 },
    { name: 'huge.tif', size: MAX_UPLOAD_FILE_BYTES + 1 },
  ]);
  assert.equal(result.ok, false);
  assert.match(result.message, /huge\.tif/);
  assert.match(result.message, /100GB/);
});

test('checkUploadLimits rejects batches whose total exceeds the 8.5GB budget', () => {
  const half = Math.floor(MAX_UPLOAD_FILE_BYTES / 2);
  const result = checkUploadLimits([
    { name: 'a.tif', size: half },
    { name: 'b.tif', size: half },
    { name: 'c.tif', size: half },
  ]);
  assert.equal(result.ok, false);
  assert.match(result.message, /100GB/);
});

test('checkUploadLimits tolerates empty and malformed input', () => {
  assert.equal(checkUploadLimits([]).ok, true);
  assert.equal(checkUploadLimits(null).ok, true);
  assert.equal(checkUploadLimits([{ name: 'x.tif', size: NaN }]).ok, true);
});

test('isDisconnectError classifies network breaks but not HTTP failures', () => {
  const network = new Error('Network Error');
  network.code = 'ERR_NETWORK';
  assert.equal(isDisconnectError(network), true);
  assert.equal(isDisconnectError(Object.assign(new Error('x'), { disconnect: true })), true);
  assert.equal(isDisconnectError(new Error('timeout of 30000ms exceeded')), true);

  const http500 = new Error('Request failed with status code 500');
  http500.response = { status: 500 };
  assert.equal(isDisconnectError(http500), false);
  assert.equal(isDisconnectError(new Error('普通业务失败')), false);
  assert.equal(isDisconnectError(null), false);
});

test('isDisconnectError prefers the unified request kind marker over message guessing', () => {
  assert.equal(isDisconnectError(Object.assign(new Error('x'), { kind: 'network' })), true);
  // http/backend/auth/aborted 都是"已到达服务器或主动取消"，不是断链
  assert.equal(isDisconnectError(Object.assign(new Error('x'), { kind: 'http' })), false);
  assert.equal(isDisconnectError(Object.assign(new Error('x'), { kind: 'backend' })), false);
  assert.equal(isDisconnectError(Object.assign(new Error('x'), { kind: 'auth' })), false);
  // 用户取消上传不能被误判为断链（否则提示语会谎称"任务可能仍在执行"）
  assert.equal(isDisconnectError(Object.assign(new Error('请求已取消'), { kind: 'aborted' })), false);
});
