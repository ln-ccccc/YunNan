// 上传进度聚合纯函数（S2，2026-09-22）：速率/剩余时长估算与文案。
// 滑动窗口速率：只用最近 N 个采样点，早期抖动不污染估算。

export const SPEED_WINDOW_SAMPLES = 8;

/**
 * 记录采样点并估算速率（字节/秒）。
 * samples: [{at(ms), bytes}] 升序；push 后裁剪到窗口。
 * 返回 { samples, bytesPerSecond | null }——样本不足或时间倒流时 null。
 */
export function pushProgressSample(samples, at, bytes) {
  const list = Array.isArray(samples) ? [...samples] : [];
  const last = list[list.length - 1];
  if (!Number.isFinite(at) || !Number.isFinite(bytes) || (last && bytes < last.bytes)) {
    return { samples: list, bytesPerSecond: null };
  }
  list.push({ at, bytes });
  while (list.length > SPEED_WINDOW_SAMPLES) list.shift();
  if (list.length < 2) return { samples: list, bytesPerSecond: null };
  const first = list[0];
  const dt = (at - first.at) / 1000;
  const db = bytes - first.bytes;
  if (dt <= 0 || db <= 0) return { samples: list, bytesPerSecond: null };
  return { samples: list, bytesPerSecond: db / dt };
}

export function formatBytes(bytes) {
  // null/undefined 显式拒绝（Number(null)===0 会伪装成合法零值）
  if (bytes === null || bytes === undefined) return '—';
  const size = Number(bytes);
  if (!Number.isFinite(size) || size < 0) return '—';
  if (size >= 1024 * 1024 * 1024) return `${(size / (1024 * 1024 * 1024)).toFixed(2)}GB`;
  if (size >= 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)}MB`;
  if (size >= 1024) return `${(size / 1024).toFixed(1)}KB`;
  return `${Math.round(size)}B`;
}

export function formatDuration(seconds) {
  const total = Number(seconds);
  if (!Number.isFinite(total) || total < 0) return '—';
  const s = Math.round(total);
  if (s < 60) return `${s}秒`;
  const m = Math.floor(s / 60);
  const rest = s % 60;
  if (m < 60) return `${m}分${String(rest).padStart(2, '0')}秒`;
  const h = Math.floor(m / 60);
  return `${h}时${String(m % 60).padStart(2, '0')}分`;
}

/**
 * 进度文案：`43% · 2.4GB/5.6GB · 预计剩余 03:12`
 * 速率未知（样本不足）时省略剩余时长段。
 */
export function buildProgressText(doneBytes, totalBytes, bytesPerSecond) {
  const done = Number(doneBytes) || 0;
  const total = Number(totalBytes) || 0;
  const percent = total > 0 ? Math.min(99, Math.floor((done / total) * 100)) : 0;
  const sizePart = total > 0 ? `${formatBytes(done)}/${formatBytes(total)}` : formatBytes(done);
  const speed = Number(bytesPerSecond);
  if (total > 0 && Number.isFinite(speed) && speed > 0) {
    const remaining = Math.max(0, (total - done) / speed);
    return `${percent}% · ${sizePart} · 预计剩余 ${formatDuration(remaining)}`;
  }
  return `${percent}% · ${sizePart}`;
}
