import axios from 'axios';
import { isSafeIncomingTiffStorageKey } from './projectWorkspaceHelpers.js';
import {
  DEFAULT_CHUNK_SIZE_BYTES,
  computeUploadKey,
  planChunks,
  sha256Hex,
  sumReceivedBytes,
} from '../utils/basemapChunking.js';


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
    // 底图网页上传（2026-10-31）：octet-stream 流式直传 BFF（转发到后端落盘
    // incoming/），走注入的 http 封装保持可测；URL 已 encodeURIComponent 防注入
    uploadBasemap: (projectId, file, onProgress) => data(http.post(
      url(`/api/projects/${projectId}/spatial/basemap-upload?filename=${encodeURIComponent(file.name)}`),
      file,
      {
        headers: { 'Content-Type': 'application/octet-stream' },
        onUploadProgress: (event) => {
          if (onProgress && event.total) onProgress(Math.round((event.loaded / event.total) * 100));
        },
      },
    )),
    // 底图分片上传（2026-11-01 后续项①，>64MB 自动分流）：init → 跳过已收分块
    // → 顺序 slice+sha256 → complete 合并落盘 incoming/。断点续传：同一文件的
    // 会话按内容摘要幂等，init 返回已收分块直接跳过；done 幂等复用上次结果。
    uploadBasemapResumable: async (projectId, file, { onProgress, signal } = {}) => {
      const uploadKey = await computeUploadKey(file);
      const initState = await data(http.post(
        url(`/api/projects/${projectId}/spatial/basemap-upload/init`),
        {
          upload_key: uploadKey,
          filename: file.name,
          total_size: file.size,
          mime: 'application/octet-stream',
          chunk_size: DEFAULT_CHUNK_SIZE_BYTES,
        },
      ));
      const received = new Set(initState?.received || []);
      const plan = planChunks(file.size, initState?.chunk_size || DEFAULT_CHUNK_SIZE_BYTES);
      if (plan.length === 0) throw new Error(`底图 ${file.name} 无法规划分片（大小 ${file.size}）`);
      const totalBytes = file.size;
      let doneBytes = sumReceivedBytes(plan, received);
      // onProgress 语义统一为 0-100 百分比（此前混传 (doneBytes, totalBytes)
      // 二元组，上层把它当百分比拼接出 5368709120% 一类异常数字——2026-11-01 审查项）
      onProgress?.(Math.round((doneBytes / totalBytes) * 100));

      for (const chunk of plan) {
        if (signal?.aborted) throw Object.assign(new Error('上传已取消'), { kind: 'aborted' });
        if (received.has(chunk.index)) continue;
        const blob = file.slice(chunk.start, chunk.start + chunk.size);
        const sha = await sha256Hex(blob);
        await data(http.post(
          url(`/api/projects/${projectId}/spatial/basemap-upload/chunk/${encodeURIComponent(initState.session_id)}/${chunk.index}`),
          blob,
          {
            headers: {
              'Content-Type': 'application/octet-stream',
              'X-Chunk-Sha256': sha,
            },
            onUploadProgress: (event) => {
              if (onProgress && event.total) onProgress(Math.round((doneBytes + event.loaded) / totalBytes * 100));
            },
          },
        ));
        doneBytes += chunk.size;
        onProgress?.(Math.round((doneBytes / totalBytes) * 100));
      }

      const completeResult = await data(http.post(
        url(`/api/projects/${projectId}/spatial/basemap-upload/complete/${encodeURIComponent(initState.session_id)}`),
        { filename: file.name },
      ));
      onProgress?.(100);
      return completeResult;
    },
    registerBasemap: (projectId, payload) => data(http.post(
      url(`/api/projects/${projectId}/spatial/basemaps`),
      payload,
    )),
    // 瓦片集直挂登记（2026-11-01 后续项③）：甲方预切瓦片包跳过切片直接激活
    registerBasemapTileset: (projectId, payload) => data(http.post(
      url(`/api/projects/${projectId}/spatial/basemap-tileset-register`),
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
