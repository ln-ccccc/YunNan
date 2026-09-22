import axios from 'axios';
import { isSafeIncomingTiffStorageKey } from './projectWorkspaceHelpers.js';


export function createProjectWorkspaceApi({ http = axios, baseUrl = '' } = {}) {
  const url = (path) => `${baseUrl}${path}`;
  const data = async (request) => {
    const response = await request;
    if (response?.data?.success === false) {
      throw new Error(response.data.msg || '项目请求失败');
    }
    return response?.data?.data ?? response?.data;
  };
  const rejectUnsafePathField = (payload, field) => {
    if (Object.hasOwn(payload || {}, field)) {
      throw new Error(`不支持 ${field}，请使用项目受控存储`);
    }
    return payload || {};
  };

  return {
    loadProjects: () => data(http.get(url('/api/projects'))),
    loadOverview: (projectId) => data(http.get(url(`/api/projects/${projectId}/overview`))),
    loadAssets: (projectId, params = {}) => data(http.get(
      url(`/api/projects/${projectId}/assets`),
      { params },
    )),
    loadSpatial: (projectId) => data(http.get(url(`/api/projects/${projectId}/spatial`))),
    loadActivity: (projectId) => data(http.get(url(`/api/projects/${projectId}/timeline`))),
    loadExports: (projectId) => data(http.get(url(`/api/projects/${projectId}/exports`))),
    loadSnapshots: (projectId) => data(http.get(url(`/api/projects/${projectId}/backups`))),
    loadMineOptions: (projectId) => data(http.get(url(`/api/projects/${projectId}/geojson`))),
    createProject: (payload) => data(http.post(url('/api/projects'), payload)),
    updateProject: (projectId, payload) => data(http.patch(url(`/api/projects/${projectId}`), payload)),
    previewMineVector: (projectId, payload) => data(http.post(
      url(`/api/projects/${projectId}/spatial/mines/preview`),
      payload,
    )),
    importMineVector: (projectId, payload) => data(http.post(
      url(`/api/projects/${projectId}/spatial/mines`),
      payload,
    )),
    listBasemapCandidates: (projectId) => data(http.get(
      url(`/api/projects/${projectId}/spatial/basemap-candidates`),
    )),
    registerBasemap: (projectId, payload) => data(http.post(
      url(`/api/projects/${projectId}/spatial/basemaps`),
      payload,
    )),
    retrySpatialJob: (projectId, jobId) => data(http.post(
      url(`/api/projects/${projectId}/spatial/jobs/${jobId}/retry`),
      {},
    )),
    cancelSpatialJob: (projectId, jobId) => data(http.post(
      url(`/api/projects/${projectId}/spatial/jobs/${jobId}/cancel`),
      {},
    )),
    createExport: (projectId, payload) => data(http.post(
      url(`/api/projects/${projectId}/exports`),
      rejectUnsafePathField(payload, 'output_dir'),
    )),
    createSnapshot: (projectId) => data(http.post(url(`/api/projects/${projectId}/backups`), {})),
    registerDataset: (projectId, payload) => {
      const safePayload = rejectUnsafePathField(payload, 'file_path');
      if (!isSafeIncomingTiffStorageKey(safePayload.storage_key)) {
        throw new Error('storage_key 必须是 incoming/ 目录下的受支持栅格文件（tif/tiff/img/jp2）');
      }
      return data(http.post(url(`/api/projects/${projectId}/datasets`), safePayload));
    },
    archiveProject: (projectId) => data(http.post(url(`/api/projects/${projectId}/archive`), {})),
    restoreProject: (projectId) => data(http.post(url(`/api/projects/${projectId}/restore`), {})),
    deleteProject: (projectId) => data(http.delete(url(`/api/projects/${projectId}`))),
    batchArchiveProjects: (projectIds) => data(http.post(url('/api/projects/batch/archive'), { project_ids: projectIds })),
    batchDeleteProjects: (projectIds) => data(http.post(url('/api/projects/batch/delete'), { project_ids: projectIds })),
    importBackupManifest: (projectId, manifest) => data(http.post(url(`/api/projects/${projectId}/backups/import`), { manifest })),
    restoreSnapshot: (projectId, snapshotId) => data(http.post(
      url(`/api/projects/${projectId}/backups/${snapshotId}/restore`),
      {},
    )),
  };
}
