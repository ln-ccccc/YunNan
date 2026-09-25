import axios from 'axios';
import { onScopeDispose, ref } from 'vue';

import { pollInferenceJob } from '../services/inferencePolling.js';
import { createProjectWorkspaceApi } from '../projectWorkspace/projectWorkspaceApi.js';

const rawMinerApiBase = import.meta.env.VITE_MINER_API_BASE_URL;
const MINER_API_BASE_URL = rawMinerApiBase ? String(rawMinerApiBase).replace(/\/$/, '') : '';
const apiUrl = (path) => `${MINER_API_BASE_URL}${path}`;

// M5.3：项目推理编排（发起/轮询/取消/就绪影像加载）从 useMineData 抽出，
// 地图工作台与智能解译页共用同一实现，避免双份漂移。
// projectId 支持数值、ref 或 getter，均由调用方保证项目上下文有效。
export function useProjectInference(resolveProjectId) {
  const getProjectId = () => Number(typeof resolveProjectId === 'function' ? resolveProjectId() : resolveProjectId);

  const inferenceRunning = ref(false);
  const inferenceResult = ref(null);
  const inferenceError = ref('');
  const imageryAssets = ref([]);
  const imageryAssetsLoading = ref(false);
  const imageryAssetsError = ref('');

  // 推理轮询取消位：组件销毁后若不取消，旧轮询会以固定间隔无限打后端
  let inferencePollCancelled = false;
  onScopeDispose(() => {
    inferencePollCancelled = true;
  });

  const projectApi = createProjectWorkspaceApi({
    baseUrl: MINER_API_BASE_URL,
  });

  const loadImageryAssets = async () => {
    const projectId = getProjectId();
    imageryAssetsLoading.value = true;
    imageryAssetsError.value = '';
    try {
      const response = await projectApi.loadAssets(projectId, { type: 'imagery', status: 'ready' });
      imageryAssets.value = response?.items || [];
    } catch (error) {
      imageryAssets.value = [];
      imageryAssetsError.value = error?.message || '推理影像加载失败';
    } finally {
      imageryAssetsLoading.value = false;
    }
  };

  const runProjectInference = async ({ datasetId, year = '', device = 'auto' } = {}) => {
    inferenceRunning.value = true;
    inferenceError.value = '';
    inferenceResult.value = null;
    inferencePollCancelled = false;
    try {
      const payload = {
        project_id: getProjectId(),
        dataset_id: Number(datasetId),
        device,
      };
      if (year) payload.year = year;
      const res = await axios.post(apiUrl('/api/inference/jobs'), payload);
      const createdJob = res?.data?.data;
      if (!createdJob?.id) throw new Error('推理任务创建后未返回任务编号');
      inferenceResult.value = createdJob;
      const terminalJob = await pollInferenceJob({
        jobId: createdJob.id,
        getJob: async (jobId) => {
          const jobRes = await axios.get(apiUrl(`/api/inference/jobs/${encodeURIComponent(jobId)}`));
          return jobRes?.data?.data;
        },
        onUpdate: (job) => {
          inferenceResult.value = job;
        },
        isCancelled: () => inferencePollCancelled,
      });
      if (terminalJob.status === 'failed' || terminalJob.status === 'cancelled') {
        throw new Error(terminalJob?.error?.message || `推理任务${terminalJob.status === 'cancelled' ? '已取消' : '失败'}`);
      }
      return terminalJob;
    } catch (e) {
      if (e?.cancelled) {
        // 组件已销毁：不再写状态，静默退出轮询链
        throw e;
      }
      inferenceError.value = e?.response?.data?.msg || e?.response?.data?.error || e?.message || '推理任务执行失败';
      throw e;
    } finally {
      if (!inferencePollCancelled) {
        inferenceRunning.value = false;
      }
    }
  };

  return {
    inferenceRunning,
    inferenceResult,
    inferenceError,
    imageryAssets,
    imageryAssetsLoading,
    imageryAssetsError,
    loadImageryAssets,
    runProjectInference,
  };
}
