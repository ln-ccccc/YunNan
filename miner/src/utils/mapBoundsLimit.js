// 项目底图视野限制：底图 bounds 与矿山 bounds 取并集、外扩 padRatio 后，
// 供 Leaflet maxBounds 硬夹持（maxBoundsViscosity=1），
// 防止拖出底图覆盖后整屏瓦片 404（2026-10-27 复测观察）。
// 矿山面可局部越出底图（登记仅要求相交），故必须并集而非仅底图 bounds。

const MIN_SPAN_DEG = 0.02;

function isFiniteNumber(value) {
  return typeof value === 'number' && Number.isFinite(value);
}

function normalizeBoundsArray(bounds) {
  if (!Array.isArray(bounds) || bounds.length < 4) return null;
  const [west, south, east, north] = bounds;
  if (![west, south, east, north].every(isFiniteNumber)) return null;
  if (west >= east || south >= north) return null;
  if (south < -90 || north > 90 || west < -180 || east > 180) return null;
  return { west, south, east, north };
}

function normalizeBoundsObject(bounds) {
  if (!bounds) return null;
  const { west, south, east, north } = bounds;
  return normalizeBoundsArray([west, south, east, north]);
}

export function normalizeProjectBounds(mapBounds) {
  return normalizeBoundsArray(mapBounds);
}

export function projectBoundsLimit(mapBounds, mineBounds, padRatio = 0.12) {
  const base = normalizeBoundsArray(mapBounds);
  const extra = normalizeBoundsObject(mineBounds);
  if (!base) return null;
  let { west, south, east, north } = base;
  if (extra) {
    west = Math.min(west, extra.west);
    south = Math.min(south, extra.south);
    east = Math.max(east, extra.east);
    north = Math.max(north, extra.north);
  }
  // 外扩比例按当前跨度取值；极小/零跨度（手绘小底图）保证最小绝对外扩，
  // 否则 maxBounds 与 fitBounds 视口重合会把地图锁死到无法交互
  const padX = Math.max((east - west) * padRatio, MIN_SPAN_DEG);
  const padY = Math.max((north - south) * padRatio, MIN_SPAN_DEG);
  west = Math.max(-180, west - padX);
  east = Math.min(180, east + padX);
  south = Math.max(-90, south - padY);
  north = Math.min(90, north + padY);
  return { west, south, east, north };
}
