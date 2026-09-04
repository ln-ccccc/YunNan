import { flashHistoryGetPage, historyGetPage } from "@/api/history"
import global from '@/global'
import { showFullScreenLoading } from "@/utils/loading";
import { getKmlRoiJob, kmlRoiInfer } from "@/api/upload";
import {
  buildProjectInferenceCards,
  buildInterpretationHistoryCards,
  groupUploadSourcesByTiff
} from "@/utils/interpretationContext.mjs";

const inferenceTerminalStatuses = new Set([
  'succeeded',
  'succeeded_with_fallback',
  'partial_failed',
  'failed',
  'cancelled'
]);

const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function waitForKmlRoiJob(jobId) {
  while (true) {
    const response = await getKmlRoiJob(jobId);
    const job = response?.data?.data;
    if (!job?.status) throw new Error("推理任务查询未返回有效状态");
    if (inferenceTerminalStatuses.has(job.status)) return job;
    await wait(1000);
  }
}

function getUploadImg(type) {
  if (type === '地物分类') {
    Promise.all([
      flashHistoryGetPage(1, 20, this.projectId).catch(() => ({ data: { data: [] } })),
      historyGetPage(1, 20, type).catch(() => ({ data: { data: [] } })),
    ]).then(([projectResponse, standaloneResponse]) => {
      this.imgArr = buildInterpretationHistoryCards(
        projectResponse?.data?.data || [],
        standaloneResponse?.data?.data || [],
        global.BASEURL,
      );
      this.isUpload = this.imgArr.length !== 0;
    }).catch(() => { });
    return;
  }
  historyGetPage(1, 20, type).then((res) => {
    this.imgArr = (res.data.data || []).map((item, idx) => ({
      ...item,
      display_index: idx + 1,
      before_img: global.BASEURL + item.before_img,
      after_img: global.BASEURL + item.after_img
    }));
    this.isUpload = this.imgArr.length !== 0;
  }).catch(() => { })
}

function goCompress(type, num) {
  this.historyGetPage(1, num, type).then((res) => {
    this.atchDownload(
      res.data.data.map((item) => {
        return { after_img: item.after_img, id: item.id };
      })
    );
  }).catch(() => { });
}

function upload(type, funUrl) {
  if (this.fileList.length === 0) {
    this.$message.error("请上传图片！");
    return;
  }

  const formData = new FormData();
  const isSegmentation = funUrl === 'semantic_segmentation';
  const roiYear = isSegmentation ? String(this.roiYear || '').trim() : '';
  if (isSegmentation) {
    if (!/^\d{4}$/.test(roiYear)) {
      this.$message.error("请先填写4位年份（YYYY），用于KML ROI结果命名");
      return;
    }
  }

  formData.append("type", type);
  // 地物分类默认开启大图切分，不再提供按钮
  if (isSegmentation) {
    formData.append("isSlice", true);
  } else if (this.isSlice) {
    formData.append("isSlice", this.isSlice);
  }

  for (const item of this.fileList) {
    const rawFile = item?.raw || item;
    if (!rawFile) continue;
    formData.append("files", rawFile, rawFile.name);
  }

  if (isSegmentation) formData.append("keepRawTiff", 'true');

  this.createSrc(formData).then((res) => {
    const uploadItems = res.data.data || [];
    this.uploadSrc.list = uploadItems.map((item) => item.src);

    const sourcesByTiff = groupUploadSourcesByTiff(uploadItems);
    const rawTiffPaths = Array.from(sourcesByTiff.keys());

    if (isSegmentation) {
      if (rawTiffPaths.length === 0) {
        this.$message.error("地物分类仅支持 tif/tiff 影像，请重新上传");
        return;
      }
      (async () => {
        const jobs = [];
        const standaloneSources = [];
        for (const tifPath of rawTiffPaths) {
          const response = await kmlRoiInfer({
            old_tif_path: tifPath,
            new_tif_path: tifPath,
            year: roiYear,
            device: 'auto',
            ...(this.projectId ? { project_id: this.projectId } : {})
          });
          const routed = response?.data?.data || {};
          if (routed.mode === 'standalone') {
            standaloneSources.push(...(sourcesByTiff.get(tifPath) || []));
            continue;
          }
          const createdJob = routed.job || routed;
          if (!createdJob?.id) throw new Error("推理任务创建后未返回任务编号");
          jobs.push(await waitForKmlRoiJob(createdJob.id));
        }

        let standaloneHandled = false;
        if (standaloneSources.length > 0) {
          await this.imgUpload(
            { ...this.uploadSrc, list: standaloneSources },
            funUrl
          );
          standaloneHandled = true;
        }

        const flashCards = [];
        let seq = 1;
        let failedCount = 0;
        const errorMessages = [];
        const fallbackReasons = new Set();
        const syncedFids = new Set();
        jobs.forEach((job) => {
          if (job.status === 'failed' || job.status === 'cancelled') {
            errorMessages.push(job?.error?.message || `任务${job.status === 'cancelled' ? '已取消' : '失败'}`);
          }
          if (job.fallback_reason) fallbackReasons.add(job.fallback_reason);
          const payload = job?.result || {};
          const failedTiles = payload.failed_tiles || [];
          if (Array.isArray(failedTiles) && failedTiles.length > 0) {
            failedCount += failedTiles.length;
            const errors = payload.tile_errors || {};
            const firstKey = Object.keys(errors)[0];
            if (firstKey && errors[firstKey]) {
              errorMessages.push(String(errors[firstKey]));
            }
          }
          (payload?.routing?.synced_fids || []).forEach((fid) => syncedFids.add(fid));
          buildProjectInferenceCards(payload.display_results || [], global.BASEURL).forEach((card) => {
            flashCards.push({ ...card, id: seq++ });
          });
        });
        const total = flashCards.length;
        flashCards.forEach((item, idx) => {
          item.id = total - idx;
        });
        if (flashCards.length > 0) {
          this.imgArr = flashCards;
          if (failedCount > 0) {
            this.$message.warning(`Flash 部分成功：${flashCards.length} 条结果，${failedCount} 个切片失败`);
          } else if (fallbackReasons.size > 0) {
            this.$message.warning(`Flash 推理完成，GPU 不可用时已回退 CPU：${Array.from(fallbackReasons).join(', ')}`);
          } else {
            this.$message.success(`已同步到当前项目 ${syncedFids.size} 个矿山`);
          }
        } else if (standaloneHandled) {
          this.$message.success("未匹配当前项目矿山，结果仅在解译平台展示");
        } else {
          const detail = errorMessages[0] ? `：${errorMessages[0].slice(0, 120)}` : "";
          this.$message.error(`Flash 推理失败，未生成任何结果${detail}`);
        }
        this.fileList = [];
        this.getMore();
      })().catch((err) => {
        const msg = err?.response?.data?.msg || err?.message || "Flash 推理失败";
        this.$message.error(msg);
      });
    } else {
      this.imgUpload(this.uploadSrc, funUrl).then(() => {
        this.fileList = [];
        this.$message.success("Pro 推理完成");
        this.getMore();
      }).catch(() => { });
    }

    if (!isSegmentation && this.uploadSrc.list.length >= 10 && type !== '场景分类') {
      this.$confirm("上传图片过多，是否压缩?", "提示", {
        confirmButtonText: "确定",
        cancelButtonText: "取消",
        type: "warning",
      })
        .then(() => {
          showFullScreenLoading('#load', '压缩中')
          this.goCompress(type, this.uploadSrc.list.length)
        }).catch(() => { })
    }
    this.$refs.upload?.clearFiles?.();
  }).catch(() => { })
}

export { getUploadImg, goCompress, upload }
