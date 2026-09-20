// 上传/断链守卫纯函数（对照江西 F2/F4/083985f 改进落地云南 GeoView）。
// 与后端闸门同源：单文件 8GB（MAX_UPLOAD_TIFF_SIZE_MB=8192），总量留 4% 余量。
export const MAX_UPLOAD_FILE_BYTES = 8192 * 1024 * 1024;
export const MAX_UPLOAD_TOTAL_BYTES = Math.floor(8192 * 1024 * 1024 * 1.04);

function formatSize(bytes) {
  if (bytes >= 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)}GB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}

/**
 * 本地上传预检：超限秒拒，不再把 GB 级无效请求白发到服务端。
 * @param {Array<{name: string, size: number}>} files
 * @returns {{ok: true} | {ok: false, message: string}}
 */
export function checkUploadLimits(files) {
  const list = Array.isArray(files) ? files : [];
  const oversized = list.find((f) => Number(f?.size) > MAX_UPLOAD_FILE_BYTES);
  if (oversized) {
    return {
      ok: false,
      message: `文件 ${oversized.name || '(未命名)'} 大小 ${formatSize(Number(oversized.size) || 0)} 超过单文件上限 8GB，请裁剪后再上传`,
    };
  }
  const total = list.reduce((sum, f) => sum + (Number(f?.size) || 0), 0);
  if (total > MAX_UPLOAD_TOTAL_BYTES) {
    return {
      ok: false,
      message: `本次上传总量 ${formatSize(total)} 超过 8.5GB 上限，请分批上传`,
    };
  }
  return { ok: true };
}

/**
 * 断链判定：axios 网络层错误（无 response）/ 显式 disconnect 标记。
 * 断链时任务可能仍在后端执行，提示语与普通失败区分（江西 083985f 语义）。
 */
export function isDisconnectError(error) {
  if (!error) return false;
  if (error.disconnect) return true;
  if (error.response) return false;
  const code = String(error.code || '');
  if (code === 'ERR_NETWORK' || code === 'ECONNABORTED') return true;
  return /network|timeout|连接|中断/i.test(String(error.message || ''));
}
