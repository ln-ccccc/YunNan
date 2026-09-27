// 全站统一时间显示格式（补零、ISO 风格）：
//   formatDateTime(value) -> 2026-09-25 14:53:19
//   formatDate(value)     -> 2026-09-25
//   formatClock(value)    -> 2026-09-27 08:29（分钟粒度，顶栏时钟用）
// 之前各组件各自 toLocaleString('zh-CN')，产生 2026/9/25 等不补零斜杠格式，
// 且同一函数在 5 个文件重复定义——统一收口于此（2026-09-27）。
const pad = (n) => String(n).padStart(2, '0');

function toDateParts(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return {
    y: String(date.getFullYear()),
    m: pad(date.getMonth() + 1),
    d: pad(date.getDate()),
    h: pad(date.getHours()),
    min: pad(date.getMinutes()),
    s: pad(date.getSeconds()),
  };
}

export function formatDate(value) {
  const parts = toDateParts(value);
  if (!parts) return String(value);
  return `${parts.y}-${parts.m}-${parts.d}`;
}

export function formatDateTime(value) {
  const parts = toDateParts(value);
  if (!parts) return String(value);
  return `${parts.y}-${parts.m}-${parts.d} ${parts.h}:${parts.min}:${parts.s}`;
}

export function formatClock(value = new Date()) {
  const parts = toDateParts(value);
  if (!parts) return String(value);
  return `${parts.y}-${parts.m}-${parts.d} ${parts.h}:${parts.min}`;
}
