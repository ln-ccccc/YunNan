export function buildGeoViewUrl(configuredUrl, currentLocation, projectId) {
  const target = new URL(configuredUrl, currentLocation.href);
  target.hostname = currentLocation.hostname;
  target.pathname = '/segmentation';
  target.search = '';
  target.hash = '';
  const value = Number(projectId);
  if (Number.isSafeInteger(value) && value > 0) {
    target.searchParams.set('project_id', String(value));
  }
  return target.toString();
}

// 编辑器直跳地址：只取配置基地址的 origin（残留路径/hash 会污染路由，
// 被 GeoView 根路由重定向吞掉——2026-09-25 实测 P1），result_id 缺省时不带参。
export function buildGeoViewEditorUrl(configuredUrl, currentLocation, projectId, resultId) {
  const origin = new URL(configuredUrl, currentLocation.href).origin;
  const target = new URL('/classification-results/editor', origin);
  const value = Number(projectId);
  if (Number.isSafeInteger(value) && value > 0) {
    target.searchParams.set('project_id', String(value));
  }
  const resultValue = Number(resultId);
  if (Number.isSafeInteger(resultValue) && resultValue > 0) {
    target.searchParams.set('result_id', String(resultValue));
  }
  return target.toString();
}
