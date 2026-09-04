# NVIDIA 通用推理 Worker 设计

## 1. 背景

当前 GeoView/Miner 的地物解译已经可以把 `device` 传递到 MMSegmentation，但存在以下系统性问题：

- GeoView 默认固定使用 CPU，Miner 允许选择 `cuda:0`，行为不一致。
- Flask 与 Miner Node API 分别直接启动 Python 推理，形成两套入口和两套结果语义。
- KML ROI 推理按瓦片重复启动子进程并重复加载模型。
- 批量上传通过 `Promise.all` 并发执行，但所有任务共用 `.tmp_kml_roi_infer`。
- Docker Compose 没有向推理服务分配 NVIDIA GPU，也没有运行时兼容检查。
- 长任务同步阻塞 HTTP 请求，没有任务状态、进度、取消或 GPU 并发限制。
- GPU 不可用、架构不兼容、MMCV CUDA 算子不可用或显存不足时，没有统一降级策略。

## 2. 目标

实现一个与具体 NVIDIA 显卡型号解耦的推理执行层：

1. 对容器内可见的 NVIDIA CUDA GPU 做运行时能力检测，不按显卡型号写死判断。
2. GPU 可用时优先使用 CUDA；不可用、不兼容或任务级 GPU 失败时回退 CPU。
3. 任何回退必须通过任务状态、API、界面和日志明确暴露，禁止静默降级。
4. 模型在 Worker 生命周期内只加载一次，并复用于多个瓦片和任务。
5. 单 GPU 默认串行执行任务，每个任务使用独立工作目录。
6. Flask 成为统一任务入口，Miner Node API 只保留兼容代理职责。
7. 保持现有 API 字段和结果目录兼容，新增异步任务接口逐步迁移前端。
8. 保留纯 CPU 部署和一键回滚能力。

## 3. 非目标

- 本次不引入 Triton、TensorRT、MMDeploy 或 Kubernetes。
- 本次不实现多机调度。
- 本次不承诺所有历史 NVIDIA GPU 都能使用当前 CUDA 镜像；运行时以锁定依赖和实际 smoke test 为准。
- 本次不重构与解译链路无关的 Miner 页面、项目管理或光谱指数业务。

## 4. 兼容性定义

“支持 NVIDIA 显卡”是指：容器内可见、被锁定的 Torch/CUDA/MMCV 运行栈支持、且能够通过真实模型 smoke test 的 CUDA GPU。

运行时不得根据 `RTX 5060`、`A100` 等型号字符串分支。判断依据依次为：

1. `torch.cuda.is_available()`；
2. 可见设备数量和所选设备索引；
3. GPU compute capability 与 Torch 编译架构信息；
4. CUDA 张量计算；
5. MMCV CUDA 算子导入和执行；
6. 模型加载与 512×512 输入 smoke test。

任一步失败且允许降级时，Worker 切换 CPU，并记录稳定错误码。

## 5. 目标架构

```text
GeoView Frontend ─┐
                  ├─> Flask Inference API ─> MySQL inference_jobs
Miner Frontend ─> Miner API proxy ─────────>          │
                                                       v
                                              Inference Worker
                                                 │         │
                                             CUDA/CPU   Job workdir
                                                 │         │
                                                 └─> Miner output volume
```

### 5.1 Flask 推理 API

负责鉴权、输入校验、任务创建、状态查询、取消标记和兼容接口。

规范接口：

```text
POST /api/inference/jobs
GET  /api/inference/jobs/{job_id}
POST /api/inference/jobs/{job_id}/cancel
GET  /api/inference/capabilities
```

现有接口继续保留，并调用同一任务服务：

```text
POST /api/analysis/kml_roi_inference
POST /api/inference/kml-roi
```

Miner 的旧接口由 Node 代理到 Flask，不再直接执行 `backend/kml_roi_infer.py`。

### 5.2 推理任务表

复用现有 MySQL，不引入 Redis。`inference_jobs` 至少包含：

- `id`
- `status`
- `request_payload`
- `requested_device`
- `effective_device`
- `fallback_reason`
- `warning_payload`
- `total_items`
- `completed_items`
- `failed_items`
- `result_payload`
- `error_code`
- `error_message`
- `cancel_requested`
- `created_at`、`started_at`、`finished_at`

任务状态：

```text
queued
running
succeeded
succeeded_with_fallback
partial_failed
failed
cancelled
```

### 5.3 Inference Worker

Worker 是唯一允许加载和执行 MMSeg 模型的服务。职责包括：

- 启动时解析有效设备并加载一次模型；
- 事务性领取一个排队任务；
- 创建 `runtime/inference_jobs/{job_id}`；
- 生成 ROI 瓦片；
- 使用已加载模型依次处理瓦片；
- 持续更新任务进度；
- 将完整结果原子写入正式结果卷；
- 清理成功任务工作目录，保留失败现场；
- 响应取消标记；
- GPU 故障时执行受控重试和 CPU 降级。

单 GPU 初始只启一个 Worker，`INFERENCE_MAX_CONCURRENCY=1`。多 GPU 仅通过容器可见设备选择扩展，不在本次增加自定义调度器。

## 6. 设备配置与解析

新增环境变量：

```text
INFERENCE_ACCELERATOR=auto
INFERENCE_CPU_FALLBACK=true
INFERENCE_GPU_DEVICE=0
INFERENCE_MAX_CONCURRENCY=1
INFERENCE_JOB_TIMEOUT_SECONDS=3600
INFERENCE_KEEP_FAILED_WORKDIR=true
```

规则：

- `auto`：优先 GPU，失败时按配置回退 CPU。
- `cpu`：强制 CPU。
- 客户端现有 `device=cpu|cuda:N` 字段继续接受以保持兼容，但部署配置拥有最终决定权。
- 旧的 `cuda:N` 请求被规范化为 `auto`，具体 GPU 由容器可见设备和 `INFERENCE_GPU_DEVICE` 决定。
- API 返回 `requested_device`、`effective_device`、`fallback_reason` 和 `warnings`。

稳定降级错误码：

```text
GPU_NOT_VISIBLE
GPU_RUNTIME_UNAVAILABLE
GPU_ARCH_UNSUPPORTED
MMCV_CUDA_OP_UNAVAILABLE
MODEL_GPU_LOAD_FAILED
CUDA_OUT_OF_MEMORY
GPU_INFERENCE_FAILED
```

## 7. GPU 故障处理

### 7.1 启动阶段失败

如果 GPU 检测、CUDA 算子或模型 smoke test 失败：

1. 记录完整服务端日志；
2. 初始化 CPU 模型；
3. Worker 标记为 `degraded`；
4. capabilities API 和前端显示降级原因；
5. 后续任务使用 CPU，并标记 `succeeded_with_fallback`。

### 7.2 任务阶段 OOM

1. 清理 CUDA 缓存；
2. 以最小批次重试当前瓦片一次；
3. 再次 OOM 时重启当前任务的 CPU 执行；
4. 任务成功则标记 `succeeded_with_fallback`，否则标记 `failed`；
5. 禁止继续在不健康 GPU 上接收下一任务，直到 Worker 完成重新检测或重启。

### 7.3 单瓦片失败

单瓦片错误不终止整个任务。成功结果继续写入，最终状态为 `partial_failed`，并返回逐瓦片错误。

## 8. 工作目录和结果一致性

- 禁止使用共享 `.tmp_kml_roi_infer`。
- 每个任务创建不可预测的 UUID 目录。
- 输入路径必须位于配置的上传根目录或只读数据根目录。
- 输出根目录由服务端配置，客户端不能覆盖。
- FID、年份和结果文件名执行白名单校验。
- 结果先写 staging 目录，完成后原子移动到结果卷。
- KML 合并在推理结果提交成功后执行，使用文件锁和原子替换。
- `_parse_last_json` 无有效 JSON 时必须报错，禁止返回伪成功。

## 9. 模型执行方式

短期先把多个瓦片合并为一次推理调用，消除逐瓦片进程和模型加载。

目标形态是 Worker 进程直接导入 MMSeg：

1. `init_model` 只在 Worker 初始化或设备切换时调用；
2. 任务内复用同一个模型对象；
3. 保持单瓦片处理以控制显存，文件列表只作为任务批次；
4. 记录模型加载次数和任务耗时；
5. 移除运行时修改 `mmdet/__init__.py` 的补丁，改用锁定依赖。

## 10. Docker 与离线部署

保留 CPU 安全的基础编排，新增 GPU override：

```text
docker-compose.prod.yml
docker-compose.gpu.yml
```

基础编排不请求 GPU，因此没有 NVIDIA Runtime 的主机仍可启动并使用 CPU。GPU override 只给 `inference-worker` 分配 NVIDIA GPU。

离线包需要包含：

- 锁定版本的 Torch、TorchVision、MMCV、MMEngine、MMSeg；
- 已构建的 MMCV CUDA 算子；
- 模型权重；
- 镜像摘要和依赖清单；
- GPU 部署前检查脚本；
- NVIDIA Container Toolkit 离线安装说明；
- CPU 和 GPU 两套启动、验证及回滚命令。

## 11. 前端行为

GeoView 和 Miner 都改为：

1. 提交任务并获得 `job_id`；
2. 定期查询进度；
3. 显示 `排队中/运行中/成功/降级成功/部分失败/失败/已取消`；
4. 显示实际设备和降级原因；
5. 禁止通过 `Promise.all` 同时运行多个完整推理任务；
6. 保留强制 CPU 选项，GPU 使用“自动”而不是硬编码 `cuda:0`。

## 12. 安全与错误语义

- 全局异常处理保留正确 HTTP 4xx/5xx。
- API 返回稳定错误码和可读消息，不返回堆栈。
- 服务端日志包含 job id、阶段、设备、异常和下一步排查方向。
- 数据库密码移出 Compose 明文配置。
- MySQL 宿主机端口默认不暴露。
- 推理超时、取消和容器退出都必须把运行中任务恢复为可诊断状态。

## 13. 测试与验收

### 13.1 单元测试

- 设备解析：GPU 可用、不可见、不支持、算子失败、强制 CPU。
- 降级错误码和任务状态转换。
- 每任务独立工作目录。
- 模型只加载一次。
- 多瓦片中单瓦片失败。
- 路径越界拒绝。
- 旧 API 字段兼容。

### 13.2 集成测试

- 无 GPU 容器完成 CPU 推理并显示降级原因。
- GPU 容器完成 Torch CUDA、MMCV CUDA 和真实模型 smoke test。
- 三个并发提交任务在单 GPU 上顺序执行，无目录冲突。
- GPU OOM 模拟后回退 CPU。
- Worker 重启后未完成任务可恢复或明确失败。
- Node 兼容接口与 Flask 规范接口返回一致结果。

### 13.3 结果一致性

使用固定黄金数据集比较 CPU 与 GPU：

- 输出尺寸、类别集合和无效区域完全一致；
- 类别图像素一致率不低于 99.5%；
- 类别占比和变化矩阵差异在测试报告中量化；
- 首轮只启用 FP32，AMP 作为后续独立优化重新验收。

## 14. 发布与回滚

分阶段交付：

1. 并发、路径和状态修复；
2. 批量模型执行；
3. 设备检测和 CPU 降级；
4. 任务表与 Worker；
5. Node/Flask 入口统一；
6. 前端异步任务；
7. GPU Compose 和离线包；
8. 全量验收。

回滚方式：

- 设置 `INFERENCE_ACCELERATOR=cpu`；
- 不加载 `docker-compose.gpu.yml`；
- 保留旧 API 兼容包装至少一个发布周期；
- 数据库迁移只新增任务表，不修改现有业务表。

## 15. 完成标准

- GPU 选择不包含任何具体 NVIDIA 型号判断。
- GPU 可用时自动使用 CUDA；异常时明确回退 CPU。
- 模型在 Worker 生命周期内只加载一次。
- 单 GPU 不并发运行多个任务。
- 任务工作目录完全隔离。
- 两套前端和两个兼容 API 使用同一任务服务。
- 旧结果目录和核心响应字段保持兼容。
- CPU、GPU、降级、部分失败、取消和恢复均有自动化测试。
- 离线环境具备完整部署、诊断和回滚文档。
