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
