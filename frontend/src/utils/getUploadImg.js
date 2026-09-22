import { flashHistoryGetPage, historyGetPage } from "@/api/history"
import global from '@/global'
import { h, ref } from 'vue'
import { ElNotification } from 'element-plus'
import { showFullScreenLoading } from "@/utils/loading";
import { persistentNotification } from "@/utils/persistentNotification.js";
import { kmlRoiInfer } from "@/api/upload";
import {
  cancelInferenceJob,
  waitForInferenceJob,
} from "@/api/inference.js";
import {
  buildProjectInferenceCards,
  buildInterpretationHistoryCards,
  groupUploadSourcesByTiff
} from "@/utils/interpretationContext.mjs";
import { checkUploadLimits, isDisconnectError } from "@/utils/uploadGuards.mjs";

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

// 上传进行位：模块级单例——组件随路由卸载不重置，防止路由往返触发并发两组
// 8GB 上传（江西 F4 同款教训：组件内 state 守卫在切页后失效）
let uploadInFlight = false;

function upload(type, funUrl) {
  if (this.fileList.length === 0) {
    this.$message.error("请上传图片！");
    return;
  }
  if (uploadInFlight) {
    this.$message.warning("已有一次上传正在进行，请等待完成或先取消");
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

  // 本地预检（江西 F2 同款）：超限秒拒，不再把 GB 级无效请求白发到服务端
  const rawFiles = [];
  for (const item of this.fileList) {
    const rawFile = item?.raw || item;
    if (rawFile) rawFiles.push(rawFile);
  }
  const limits = checkUploadLimits(rawFiles.map((file) => ({ name: file.name, size: file.size })));
  if (!limits.ok) {
    this.$message.error(limits.message);
    return;
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

  // 上传进度 + 可取消（江西 F2 同款）：duration=0 常驻通知，结束/取消必须显式关闭
  const controller = new AbortController();
  const percent = ref(0);
  const progressBody = {
    name: 'UploadProgressBody',
    setup() {
      return () => h('div', { style: 'display:flex;align-items:center;gap:12px;' }, [
        h('span', `已上传 ${percent.value}%`),
        h('button', {
          type: 'button',
          style: 'border:none;border-radius:4px;padding:4px 10px;cursor:pointer;color:#fff;background:#409eff;',
          onClick: () => controller.abort(),
        }, '取消上传'),
      ]);
    },
  };
  const notification = persistentNotification({
    title: '影像上传中',
    message: h(progressBody),
    duration: 0,
    showClose: false,
  });
  uploadInFlight = true;

  this.createSrc(formData, {
    signal: controller.signal,
    onUploadProgress: (event) => {
      if (event?.total) {
        percent.value = Math.min(99, Math.round((event.loaded / event.total) * 100));
      }
    },
  }).then((res) => {
    notification.close();
    uploadInFlight = false;
    percent.value = 100;
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
        const total = rawTiffPaths.length;
        // 推理进度通知：常驻展示第几张/任务号；取消走后端 cancel 端点
        // （置 cancel_requested 标记，worker 在切片间检查并终止落 cancelled 终态），
        // 轮询观察到 cancelled 即收尾，不再提交后续影像
        const inferProgress = ref(`第 1/${total} 张推理中`);
        let activeJobId = '';
        let cancelRequested = false;
        const inferBody = {
          name: 'InferProgressBody',
          setup() {
            return () => h('div', { style: 'display:flex;align-items:center;gap:12px;' }, [
              h('span', inferProgress.value),
              h('button', {
                type: 'button',
                style: 'border:none;border-radius:4px;padding:4px 10px;cursor:pointer;color:#fff;background:#f56c6c;',
                onClick: () => {
                  if (!activeJobId || cancelRequested) return;
                  cancelRequested = true;
                  inferProgress.value = '已请求取消，等待任务停止…';
                  cancelInferenceJob(activeJobId).catch(() => {
                    // 取消请求失败（网络/任务已结束）：恢复按钮可重试
                    cancelRequested = false;
                    inferProgress.value = '取消请求未送达，可重试取消';
                  });
                },
              }, '取消推理'),
            ]);
          },
        };
        const inferNotification = persistentNotification({
          title: 'KML ROI 推理中',
          message: h(inferBody),
          duration: 0,
          showClose: false,
        });
        try {
          for (let index = 0; index < rawTiffPaths.length; index++) {
            const tifPath = rawTiffPaths[index];
            inferProgress.value = `第 ${index + 1}/${total} 张推理中`;
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
            // 新任务重臂取消：上一任务若在取消请求送达前已终态（后端对终态取消是 200 空操作），
            // cancelRequested 残留 true 会让本任务及后续的取消按钮永久失效
            activeJobId = createdJob.id;
            cancelRequested = false;
            inferProgress.value = `第 ${index + 1}/${total} 张推理中（任务 ${createdJob.id}）`;
            const job = await waitForInferenceJob(createdJob.id);
            jobs.push(job);
            // 用户取消或后端判死后不再提交后续影像，已完成部分照常展示
            if (job.status === 'cancelled') break;
          }
        } finally {
          inferNotification.close();
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
          buildProjectInferenceCards(
            payload.display_results || [],
            global.BASEURL,
            this.projectId || payload?.routing?.project_id,
          ).forEach((card) => {
            flashCards.push({ ...card, id: seq++ });
          });
        });
        const cardsTotal = flashCards.length;
        flashCards.forEach((item, idx) => {
          item.id = cardsTotal - idx;
        });
        const wasCancelled = jobs.some((job) => job.status === 'cancelled');
        if (flashCards.length > 0) {
          this.imgArr = flashCards;
          if (failedCount > 0) {
            this.$message.warning(`Flash 部分成功：${flashCards.length} 条结果，${failedCount} 个切片失败`);
          } else if (fallbackReasons.size > 0) {
            this.$message.warning(`Flash 推理完成，GPU 不可用时已回退 CPU：${Array.from(fallbackReasons).join(', ')}`);
          } else {
            this.$message.success(`已同步到当前项目 ${syncedFids.size} 个矿山`);
          }
          if (wasCancelled) {
            this.$message.info('已取消推理，已完成部分保留在历史记录');
          }
        } else if (wasCancelled) {
          // standalone 影像走的是同步通道，取消只作用于后续 job 任务，已算完的结果不因取消而作废
          this.$message.info(standaloneHandled
            ? '已取消推理，未匹配项目的影像结果已保留在历史记录'
            : '已取消推理，未生成结果');
        } else if (standaloneHandled) {
          this.$message.success("未匹配当前项目矿山，结果仅在解译平台展示");
        } else {
          const detail = errorMessages[0] ? `：${errorMessages[0].slice(0, 120)}` : "";
          this.$message.error(`Flash 推理失败，未生成任何结果${detail}`);
        }
        this.fileList = [];
        this.getMore();
      })().catch((err) => {
        // 断链语义（江西 083985f 同款）：任务可能仍在后端执行，不误报"推理失败"
        if (isDisconnectError(err)) {
          this.$message.error('连接已中断，任务可能仍在后端执行，请稍后在历史记录中查看结果');
          return;
        }
        // 非 silent 请求（kmlRoiInfer/imgUpload）拦截器已弹 toast，这里不重复
        if (err?.silent !== false) {
          const msg = err?.response?.data?.msg || err?.message || "Flash 推理失败";
          this.$message.error(msg);
        }
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
  }).catch((err) => {
    notification.close();
    uploadInFlight = false;
    // 取消优先；业务/HTTP 失败（silent 下拦截器不弹 toast）与断链在下方分支分别给出反馈
    const cancelled = err?.kind === 'aborted' || err?.code === 'ERR_ABORTED' || err?.code === 'canceled'
      || /cancel|abort/i.test(String(err?.message || ''));
    if (cancelled) {
      this.$message.info('已取消上传');
      return;
    }
    // 业务失败（kind=backend）此前既无拦截器 toast（silent）也无本地分支，全程零反馈；
    // HTTP 失败（kind=http）同理。这里统一兜底给出后端 msg（包装错误的 message 即后端 msg）
    if ((err?.kind === 'backend' || err?.kind === 'http') && err?.silent !== false) {
      this.$message.error(err?.message || err?.response?.data?.msg || '上传失败，请重试');
      return;
    }
    if (isDisconnectError(err)) {
      this.$message.error('连接已中断，请检查网络后重试');
      return;
    }
    if (err?.response) {
      this.$message.error(err?.response?.data?.msg || '上传失败，请重试');
    }
  }).finally(() => {
    // 兜底复位：then 分支已复位，防御提前 return 路径（如非 tif 提示）
    uploadInFlight = false;
  })
}

export { getUploadImg, goCompress, upload }
