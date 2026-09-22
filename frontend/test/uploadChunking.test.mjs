import assert from 'node:assert/strict';
import test from 'node:test';

import {
  CHUNK_UPLOAD_THRESHOLD_BYTES,
  DEFAULT_CHUNK_SIZE_BYTES,
  planChunks,
  sha256HexPlain,
  shouldUseChunkedUpload,
  sumReceivedBytes,
} from '../src/utils/uploadChunking.mjs';
import { MAX_UPLOAD_FILE_BYTES } from '../src/utils/uploadGuards.mjs';

test('shouldUseChunkedUpload splits at the 512MB threshold', () => {
  assert.equal(shouldUseChunkedUpload(CHUNK_UPLOAD_THRESHOLD_BYTES), false);
  assert.equal(shouldUseChunkedUpload(CHUNK_UPLOAD_THRESHOLD_BYTES + 1), true);
  assert.equal(shouldUseChunkedUpload(0), false);
  assert.equal(shouldUseChunkedUpload(NaN), false);
  assert.equal(shouldUseChunkedUpload(null), false);
});

test('planChunks produces full chunks with a remainder tail', () => {
  const mb = 1024 * 1024;
  const plan = planChunks(2.5 * mb, mb);
  assert.deepEqual(plan, [
    { index: 0, start: 0, size: mb },
    { index: 1, start: mb, size: mb },
    { index: 2, start: 2 * mb, size: 0.5 * mb },
  ]);
  // 整除时无空尾块
  assert.deepEqual(planChunks(2 * mb, mb).length, 2);
  // 单块小于块大小
  assert.deepEqual(planChunks(1, mb), [{ index: 0, start: 0, size: 1 }]);
});

test('planChunks rejects malformed inputs with an empty plan', () => {
  assert.deepEqual(planChunks(0, mb0()), []);
  assert.deepEqual(planChunks(-5, 1024), []);
  assert.deepEqual(planChunks('abc', 1024), []);
  assert.deepEqual(planChunks(1024, 0), []);
  assert.deepEqual(planChunks(undefined, undefined), []);
  function mb0() { return 1024; }
});

test('sumReceivedBytes counts only received chunks (resume progress jump)', () => {
  const plan = [
    { index: 0, start: 0, size: 10 },
    { index: 1, start: 10, size: 10 },
    { index: 2, start: 20, size: 5 },
  ];
  assert.equal(sumReceivedBytes(plan, new Set([0, 2])), 15);
  assert.equal(sumReceivedBytes(plan, [1]), 10);
  assert.equal(sumReceivedBytes(plan, new Set()), 0);
  assert.equal(sumReceivedBytes(plan, null), 0);
});

test('chunk constants stay aligned with the backend session contract', () => {
  // 后端 UPLOAD_SESSION_MIN/MAX_CHUNK_BYTES = 1MB~512MB；100GB 上限 = 1600 块默认大小
  assert.equal(DEFAULT_CHUNK_SIZE_BYTES, 64 * 1024 * 1024);
  assert.ok(DEFAULT_CHUNK_SIZE_BYTES >= 1024 * 1024, '分块不得小于后端下限 1MB');
  assert.ok(DEFAULT_CHUNK_SIZE_BYTES <= 512 * 1024 * 1024, '分块不得大于后端上限 512MB');
  assert.ok(MAX_UPLOAD_FILE_BYTES % DEFAULT_CHUNK_SIZE_BYTES === 0, '100GB 应能被默认块整除');
});

test('sha256HexPlain matches FIPS 180-4 known vectors (insecure-context fallback)', () => {
  // crypto.subtle 缺失（http://内网IP）时的回退实现必须与标准一致，
  // 否则会话标识与服务端/安全上下文版本对不上
  assert.equal(sha256HexPlain(''), 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855');
  assert.equal(sha256HexPlain('abc'), 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
  const long = 'a'.repeat(1000);
  assert.equal(sha256HexPlain(long).length, 64);
  // 与 node 原生对账（多块路径：>64 字节输入触发扩展轮）
  assert.equal(sha256HexPlain(new TextEncoder().encode(long)), sha256HexPlain(long));
});
