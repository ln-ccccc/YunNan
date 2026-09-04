const POSITIVE_INTEGER_PATTERN = /^[1-9]\d*$/;

function parsePositiveSafeInteger(value) {
  if (typeof value === 'number') {
    return Number.isSafeInteger(value) && value > 0 ? value : null;
  }
  if (typeof value !== 'string' || !POSITIVE_INTEGER_PATTERN.test(value)) {
    return null;
  }

  const parsed = Number(value);
  return Number.isSafeInteger(parsed) ? parsed : null;
}

function readSinglePositiveSafeInteger(params, key) {
  const values = params.getAll(key);
  return values.length === 1 ? parsePositiveSafeInteger(values[0]) : null;
}

export function readClassificationResultContext(search = '') {
  const params = new URLSearchParams(typeof search === 'string' ? search : '');
  const projectId = readSinglePositiveSafeInteger(params, 'project_id');
  const resultId = readSinglePositiveSafeInteger(params, 'result_id');

  return projectId && resultId ? { projectId, resultId } : null;
}

export function buildClassificationResultRoute(projectId, resultId) {
  const validProjectId = parsePositiveSafeInteger(projectId);
  const validResultId = parsePositiveSafeInteger(resultId);
  if (!validProjectId || !validResultId) return null;

  return `/classification-results/editor?project_id=${validProjectId}&result_id=${validResultId}`;
}
