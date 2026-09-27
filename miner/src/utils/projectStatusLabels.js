// 生命周期状态中文映射的唯一来源（收敛 3 处重复定义，gui-audit #7/#M4）。
// 新增消费方一律 import 本模块，禁止在组件内再建本地映射表。
export const LIFECYCLE_STATUS_LABELS = Object.freeze({
  draft: '草稿',
  active: '进行中',
  completed: '已完成',
  archived: '已归档',
});

export function formatLifecycleStatus(status) {
  return LIFECYCLE_STATUS_LABELS[status] || status || '--';
}
