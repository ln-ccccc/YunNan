// 底图分片续传规划纯函数（HTTP 编排在 projectWorkspaceApi.uploadBasemapResumable，
// 此处保持 node --test 可直接测）。自 frontend/src/utils/uploadChunking.mjs 移植
// （2026-11-01 后续项①：底图网页上传 >15GB 场景复用 100GB 分片通道）。
// 超过阈值的底图自动走 init/chunk/complete 分片通道：单发流式受浏览器与代理的
// 长连接稳定性约束；分片每块秒级完成，失败只重传单块、页面重开自动续传。

export const CHUNK_UPLOAD_THRESHOLD_BYTES = 64 * 1024 * 1024;

// 与后端 UPLOAD_SESSION_MIN/MAX_CHUNK_BYTES(1MB~512MB) 约束对齐的默认分块
export const DEFAULT_CHUNK_SIZE_BYTES = 64 * 1024 * 1024;

// 底图单发通道的业务上限（与 spatial_service.MAX_BASEMAP_UPLOAD_BYTES 一致）
export const BASEMAP_SINGLE_UPLOAD_LIMIT_BYTES = 15 * 1024 * 1024 * 1024;

// 底图分片通道的总上限（对齐 UPLOAD_SESSION_MAX_TOTAL_MB=100GB）
export const BASEMAP_CHUNKED_UPLOAD_LIMIT_BYTES = 100 * 1024 * 1024 * 1024;

export function shouldUseChunkedUpload(fileSize) {
  const size = Number(fileSize);
  return Number.isFinite(size) && size > CHUNK_UPLOAD_THRESHOLD_BYTES;
}

export function exceedsChunkedUploadLimit(fileSize) {
  const size = Number(fileSize);
  return Number.isFinite(size) && size > BASEMAP_CHUNKED_UPLOAD_LIMIT_BYTES;
}

/**
 * 生成分块计划：[{index, start, size}]，末块为余量。
 * 输入非法（非正数/非有限）返回空数组——调用方按"无法分片"处理。
 */
export function planChunks(totalSize, chunkSize) {
  const size = Number(totalSize);
  const cs = Number(chunkSize);
  if (!Number.isFinite(size) || !Number.isFinite(cs) || size <= 0 || cs <= 0) {
    return [];
  }
  const total = Math.ceil(size / cs);
  const plan = [];
  for (let index = 0; index < total; index++) {
    const start = index * cs;
    plan.push({ index, start, size: Math.min(cs, size - start) });
  }
  return plan;
}

/** 已收分块的字节数（断点续传时进度应直接跳到此处，不重复上传） */
export function sumReceivedBytes(plan, receivedSet) {
  const received = receivedSet instanceof Set ? receivedSet : new Set(receivedSet || []);
  return plan
    .filter((chunk) => received.has(chunk.index))
    .reduce((sum, chunk) => sum + chunk.size, 0);
}

// 纯 JS SHA-256（crypto.subtle 仅安全上下文存在，http://内网IP 访问时回退；
// 实现 FIPS 180-4，输入 string 或 Uint8Array，输出 hex——与 frontend 同源实现）。
const _SHA256_K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

function _rotr(x, n) {
  return ((x >>> n) | (x << (32 - n))) >>> 0;
}

export function sha256HexPlain(input) {
  const bytes = typeof input === 'string'
    ? new TextEncoder().encode(input)
    : new Uint8Array(input);
  const bitLen = bytes.length * 8;
  const padded = new Uint8Array((((bytes.length + 8) >> 6) + 1) << 6);
  padded.set(bytes);
  padded[bytes.length] = 0x80;
  const dv = new DataView(padded.buffer);
  dv.setUint32(padded.length - 4, bitLen >>> 0);
  dv.setUint32(padded.length - 8, Math.floor(bitLen / 0x100000000));

  const h = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
  const w = new Uint32Array(64);

  for (let offset = 0; offset < padded.length; offset += 64) {
    for (let i = 0; i < 16; i++) w[i] = dv.getUint32(offset + i * 4);
    for (let i = 16; i < 64; i++) {
      const s0 = _rotr(w[i - 15], 7) ^ _rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
      const s1 = _rotr(w[i - 2], 17) ^ _rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
    }
    let a = h[0], b = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], hh = h[7];
    for (let i = 0; i < 64; i++) {
      const S1 = _rotr(e, 6) ^ _rotr(e, 11) ^ _rotr(e, 25);
      const ch = (e & f) ^ (~e & g);
      const t1 = (hh + S1 + ch + _SHA256_K[i] + w[i]) >>> 0;
      const S0 = _rotr(a, 2) ^ _rotr(a, 13) ^ _rotr(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const t2 = (S0 + maj) >>> 0;
      hh = g; g = f; f = e; e = (d + t1) >>> 0;
      d = c; c = b; b = a; a = (t1 + t2) >>> 0;
    }
    h[0] = (h[0] + a) >>> 0; h[1] = (h[1] + b) >>> 0; h[2] = (h[2] + c) >>> 0; h[3] = (h[3] + d) >>> 0;
    h[4] = (h[4] + e) >>> 0; h[5] = (h[5] + f) >>> 0; h[6] = (h[6] + g) >>> 0; h[7] = (h[7] + hh) >>> 0;
  }
  return Array.from(h, (x) => x.toString(16).padStart(8, '0')).join('');
}

/** 分块 sha256：优先 crypto.subtle（安全上下文），http://内网IP 回退纯 JS 实现。
 * 字符串必须经 TextEncoder 编码——new Uint8Array(str) 会把每个字符 ToNumber
 * 成 0（NaN 截断），任何等长字符串都哈希出同一摘要，upload_key 随之撞车
 * （2026-11-01 审查 P1）。 */
export async function sha256Hex(input) {
  const bytes = typeof input === 'string'
    ? new TextEncoder().encode(input)
    : input instanceof Blob ? new Uint8Array(await input.arrayBuffer()) : new Uint8Array(input);
  if (globalThis.crypto && globalThis.crypto.subtle) {
    const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes);
    return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, '0')).join('');
  }
  return sha256HexPlain(bytes);
}

/** 会话标识：文件名+大小+修改时间的摘要（hex），同一文件天然断点续传。 */
export async function computeUploadKey(file) {
  return sha256Hex(`${file.name}:${file.size}:${file.lastModified}`);
}
