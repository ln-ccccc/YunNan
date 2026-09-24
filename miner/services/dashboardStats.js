// 修复后地类归类聚合（参照江西 miner/services/dashboardStats.js 的看板塑造职责）。
// 后端 /stats 的 landTypeList 按 NXFFX 原始组合串分桶（如 "林地,草地"），
// 多值组合把面板炸成 20+ 行；这里把组合拆开、按涉及的一级地类分别计数，
// 未知/未标注归并为一个桶固定排在末位，其余按数量降序。

const UNKNOWN_BUCKET_PATTERN = /未知|未标注|暂无/;
const CLASS_SEPARATOR_PATTERN = /[,，、;；/]+|\s+/;

export function aggregateLandTypeList(list) {
  if (!Array.isArray(list)) return [];

  const classCounts = {};
  let unknownCount = 0;

  for (const item of list) {
    const rawValue = Number(item?.value);
    if (!Number.isFinite(rawValue) || rawValue <= 0) continue;
    const rawName = String(item?.name || '').trim();
    if (!rawName || UNKNOWN_BUCKET_PATTERN.test(rawName)) {
      unknownCount += rawValue;
      continue;
    }
    const classes = rawName
      .split(CLASS_SEPARATOR_PATTERN)
      .map((part) => part.trim())
      .filter(Boolean);
    if (!classes.length) {
      unknownCount += rawValue;
      continue;
    }
    // 同一组合内重复出现的地类只计一次（如 "林地,林地"）
    for (const landClass of new Set(classes)) {
      classCounts[landClass] = (classCounts[landClass] || 0) + rawValue;
    }
  }

  const rows = Object.entries(classCounts)
    .map(([name, value]) => ({ name, value }))
    .sort((a, b) => b.value - a.value || a.name.localeCompare(b.name, 'zh-CN'));

  if (unknownCount > 0) {
    rows.push({ name: '未知', value: unknownCount });
  }
  return rows;
}
