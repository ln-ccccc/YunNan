export function buildChangeMatrixAssetPath(fid, filename) {
  const fidText = String(fid || '').trim();
  const fileText = String(filename || '').trim();
  if (!fidText || !fileText) return null;
  return `/change-matrix-outputs/${encodeURIComponent(fidText)}/${encodeURIComponent(fileText)}`;
}
