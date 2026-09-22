// 主控台路由表（M1 2026-09-22）：左导航七模块的 hash 路由。
// 仅 #/map/:id 带参数；未实现模块指向占位视图（M3/M4 逐步填充）。
export const VIEW_HASH = {
  projects: '#/projects',
  imagery: '#/imagery',
  interpretation: '#/interpretation',
  editing: '#/editing',
  data: '#/data',
  search: '#/search',
  settings: '#/settings',
};

// 左侧导航项（顺序即展示顺序）：key 必须与 resolveViewFromHash 的返回值一致
export const NAV_ITEMS = [
  { key: 'projects', label: '项目管理', hash: VIEW_HASH.projects, implemented: true },
  { key: 'imagery', label: '影像管理', hash: VIEW_HASH.imagery, implemented: false },
  { key: 'interpretation', label: '智能解译', hash: VIEW_HASH.interpretation, implemented: false },
  { key: 'editing', label: '图斑编辑', hash: VIEW_HASH.editing, implemented: false },
  { key: 'data', label: '数据管理', hash: VIEW_HASH.data, implemented: false },
  { key: 'search', label: '查询搜索', hash: VIEW_HASH.search, implemented: false },
  { key: 'settings', label: '系统设置', hash: VIEW_HASH.settings, implemented: false },
];

const STATIC_VIEWS = new Set(Object.keys(VIEW_HASH));

export function resolveViewFromHash(hashValue) {
  if (parseProjectIdFromHash(hashValue) !== null) return 'map';
  const match = String(hashValue || '').match(/^#\/([a-z]+)$/);
  if (match && STATIC_VIEWS.has(match[1])) return match[1];
  return 'projects';
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
