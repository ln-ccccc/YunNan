export function formatAreaKm2(m2) {
  if (m2 === null || m2 === undefined || m2 === '') return '—';
  const value = Number(m2);
  if (!Number.isFinite(value) || value < 0) return '—';
  if (value > 0 && value < 1000) return '< 0.001 km²';
  return `${(value / 1e6).toFixed(3)} km²`;
}
