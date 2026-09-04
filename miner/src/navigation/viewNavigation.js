export const VIEW_HASH = {
  projects: '#/projects',
};

export function resolveViewFromHash(hashValue) {
  return parseProjectIdFromHash(hashValue) !== null ? 'map' : 'projects';
}

export function parseProjectIdFromHash(hashValue) {
  const match = String(hashValue || '').match(/^#\/map\/(\d+)$/);
  if (!match) return null;
  const value = Number(match[1]);
  return Number.isSafeInteger(value) && value > 0 ? value : null;
}

export function buildMapHash(projectId) {
  const value = Number(projectId);
  if (!Number.isSafeInteger(value) || value <= 0) {
    throw new Error('打开地图必须指定有效项目 ID');
  }
  return `#/map/${value}`;
}
