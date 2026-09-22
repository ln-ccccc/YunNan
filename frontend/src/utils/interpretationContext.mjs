export function readProjectId(search = '') {
  const value = Number(new URLSearchParams(search).get('project_id'));
  return Number.isSafeInteger(value) && value > 0 ? value : null;
}

export function buildProjectRoute(path, projectId) {
  const value = Number(projectId);
  return {
    path,
    query: Number.isSafeInteger(value) && value > 0
      ? { project_id: String(value) }
      : {},
  };
}

export function groupUploadSourcesByTiff(uploadItems = []) {
  const groups = new Map();
  uploadItems.forEach((item) => {
    const tifPath = typeof item?.raw_tiff_path === 'string'
      ? item.raw_tiff_path.trim()
      : '';
    if (!tifPath || typeof item?.src !== 'string' || !item.src) return;
    const sources = groups.get(tifPath) || [];
    sources.push(item.src);
    groups.set(tifPath, sources);
  });
  return groups;
}

function backendAssetUrl(backendBaseUrl, value) {
  if (!value) return null;
  const base = String(backendBaseUrl || '').endsWith('/')
    ? String(backendBaseUrl)
    : `${backendBaseUrl}/`;
  return new URL(String(value).replace(/^\//, ''), base).toString();
}

export function buildProjectInferenceCards(displayResults = [], backendBaseUrl = '', projectId = null) {
  const validProjectId = Number(projectId);
  const cardProjectId = Number.isSafeInteger(validProjectId) && validProjectId > 0
    ? validProjectId
    : null;
  return displayResults
    .filter((item) => item?.after_img)
    .map((item, index) => ({
      id: index + 1,
      record_id: `${item.fid}|${item.year}`,
      // 项目推理产出的卡片按项目成果管理：删除入口走"请在项目中管理"拦截，
      // 而不是落进 flash 的 fid|year 误删端点（2026-09-22 契约审查 P2）
      record_source: 'project',
      type: '地物分类',
      before_img: backendAssetUrl(backendBaseUrl, item.before_img),
      after_img: backendAssetUrl(backendBaseUrl, item.after_img),
      data: {
        fid: item.fid,
        year: item.year,
        project_id: cardProjectId,
        result_id: item.result_id ?? null,
        vector_status: item.vector_status ?? null,
        vector_error: item.vector_error ?? null,
      },
    }));
}

export function buildInterpretationHistoryCards(
  projectItems = [],
  standaloneItems = [],
  backendBaseUrl = '',
) {
  const projectCards = projectItems.map((item) => ({
    ...item,
    record_source: item?.data?.mode === 'project' ? 'project' : 'flash',
    before_img: backendAssetUrl(backendBaseUrl, item.before_img),
    after_img: backendAssetUrl(backendBaseUrl, item.after_img),
  }));
  const standaloneCards = standaloneItems.map((item) => ({
    ...item,
    record_source: 'standalone',
    before_img: backendAssetUrl(backendBaseUrl, item.before_img),
    after_img: backendAssetUrl(backendBaseUrl, item.after_img),
  }));
  return [...projectCards, ...standaloneCards].map((item, index) => ({
    ...item,
    display_index: index + 1,
  }));
}
