// 分片续传规划纯函数（HTTP 编排在 api/upload.js，此处保持 node --test 可直接测）。
// 超过该阈值的文件自动走 init/chunk/complete 分片通道：
// 单发 multipart 通道受 gunicorn 单请求超时（120s）约束，不适合小时级长传；
// 分片每块秒级完成天然避开超时，且失败只重传单块、页面重开自动续传。
export const CHUNK_UPLOAD_THRESHOLD_BYTES = 512 * 1024 * 1024;

// 与后端 UPLOAD_SESSION_MIN/MAX_CHUNK_BYTES(1MB~512MB) 约束对齐的默认分块
export const DEFAULT_CHUNK_SIZE_BYTES = 64 * 1024 * 1024;

export function shouldUseChunkedUpload(fileSize) {
  const size = Number(fileSize);
  return Number.isFinite(size) && size > CHUNK_UPLOAD_THRESHOLD_BYTES;
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
