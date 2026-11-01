import assert from 'node:assert/strict';
import test from 'node:test';

import {
  BASEMAP_CHUNKED_UPLOAD_LIMIT_BYTES,
  computeUploadKey,
  exceedsChunkedUploadLimit,
  planChunks,
  sha256Hex,
  sha256HexPlain,
  shouldUseChunkedUpload,
  sumReceivedBytes,
} from '../src/utils/basemapChunking.js';

test('sha256 of strings follows FIPS 180-4 (字符串必须经 TextEncoder 编码)', async () => {
  // "abc" 的标准摘要——修复前 new Uint8Array(str) 把每个字符 ToNumber 成 0，
  // 等长字符串全部哈希出同一份全零摘要（2026-11-01 审查 P1）
  assert.equal(await sha256Hex('abc'), 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
  assert.equal(sha256HexPlain('abc'), 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
  assert.equal(
    await sha256Hex(new Uint8Array([97, 98, 99])),
    'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
  );
});

test('upload key differs for different files even when key strings are equal length', async () => {
  // 'a.tif:100:1' 与 'b.tif:200:2' 等长——修复前生成完全相同的会话标识，
  // 两个不同大文件会互相复用对方的分块会话
  const keyA = await computeUploadKey({ name: 'a.tif', size: 100, lastModified: 1 });
  const keyB = await computeUploadKey({ name: 'b.tif', size: 200, lastModified: 2 });
  assert.notEqual(keyA, keyB);
  // 同一文件重开页面仍是同一 key（断点续传的前提）
  assert.equal(keyA, await computeUploadKey({ name: 'a.tif', size: 100, lastModified: 1 }));
});

test('chunk planning and received-bytes bookkeeping stay exact', () => {
  const plan = planChunks(150, 64);
  assert.deepEqual(plan, [
    { index: 0, start: 0, size: 64 },
    { index: 1, start: 64, size: 64 },
    { index: 2, start: 128, size: 22 },
  ]);
  assert.deepEqual(planChunks(0, 64), []);
  assert.equal(sumReceivedBytes(plan, [0, 2]), 86);
  assert.equal(sumReceivedBytes(plan, []), 0);
});

test('upload channel selection respects the 64MB threshold and the 100GB ceiling', () => {
  assert.equal(shouldUseChunkedUpload(64 * 1024 * 1024), false);
  assert.equal(shouldUseChunkedUpload(64 * 1024 * 1024 + 1), true);
  assert.equal(exceedsChunkedUploadLimit(BASEMAP_CHUNKED_UPLOAD_LIMIT_BYTES), false);
  assert.equal(exceedsChunkedUploadLimit(BASEMAP_CHUNKED_UPLOAD_LIMIT_BYTES + 1), true);
});
