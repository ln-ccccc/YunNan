# NVIDIA 通用推理修复进度

## 管理规则

- 设计：`docs/superpowers/specs/2026-07-15-nvidia-inference-worker-design.md`
- 实施计划：`docs/superpowers/plans/2026-07-15-nvidia-inference-worker-implementation.md`
- 每完成一个 Task，记录修改、验证、风险和回滚点。
- 需要用户确认的事项集中记录在文末，不中途阻塞安全且可回滚的工作。
- 不修改与推理链路无关的功能。

## 当前状态

| Task | 状态 | 说明 |
| --- | --- | --- |
| 设计确认 | 已完成 | 用户批准方案 B：统一异步推理 Worker |
| 设计文档 | 已完成 | 已写入并完成占位符、一致性和范围自审 |
| 实施计划 | 已完成 | 已拆分为 10 个可验证任务 |
| Task 1 路径与工作目录 | 已完成 | 独立 UUID 目录，非空目录拒绝覆盖，输入根目录边界已封装 |
| Task 2 批量瓦片 | 已完成 | 单次子进程处理整批瓦片，结构化保留单片错误 |
| Task 3 设备解析 | 已完成 | 基于运行时能力而非型号的 CUDA 探测与显式 CPU 降级 |
| Task 4 任务 API | 已完成 | MySQL 任务表、状态机、鉴权 API 和原子领取；容器集成测试通过 |
| Task 5 Worker | 已完成 | 模型常驻、进度/取消、超时、OOM 显式回退；GPU/CPU 初始化和真实前向已验收 |
| Task 6 入口统一 | 已完成 | Node 仅代理 Flask 异步任务 API，不再启动 Python 子进程 |
| Task 7 双前端 | 已完成 | 创建任务、轮询进度、取消、有效设备和降级提示 |
| Task 8 Docker | 已完成 | CPU base + NVIDIA override、多阶段 CUDA 12.8 镜像和真实 GPU 自检均通过 |
| Task 9 运行安全 | 已完成 | HTTP 状态、凭据、路径边界和 Gunicorn 启动已收敛 |
| Task 10 验收文档 | 已完成 | 兼容性/离线部署文档、GPU smoke、Worker 前向和 CPU/GPU 黄金样例均完成 |

## 已验证基线

- `cd miner && npm test`：30/30 PASS。
- `node --check miner/server.js`：PASS。
- 推理相关 Python 文件 `py_compile`：PASS。
- Docker `MMSeg310` 环境完整后端测试：66/66 PASS（含 Flask/SQLite、输出原子发布、Worker 重启恢复、模型 smoke 与路径边界）。
- Miner 与 GeoView 两个生产构建：PASS（保留既有 bundle 体积和 Browserslist 警告）。
- 基础 Compose 与 GPU override 合并配置：PASS；基础配置未申请 NVIDIA 设备，仅 Worker override 申请 GPU。
- 旧镜像自检正确识别 CPU-only Torch，并以 `GPU_RUNTIME_UNAVAILABLE` 显式回退 CPU。
- 精简 GPU 镜像自检通过：Torch 2.7.0+cu128、CUDA 12.8、MMCV 2.1.0 CUDA ops、MMEngine 0.10.4、MMSeg 1.1.2。
- GPU 模型加载、512×512 前向和完整 Worker 初始化通过；无 GPU 授权时同镜像以 `GPU_NOT_VISIBLE` 回退 CPU。
- 固定仓库样例 CPU/GPU 对比：241,081 像素中 4 像素不同，一致率 99.99834%，类别计数一致。

## 环境限制

- 项目根目录 `.git` 存在但为空，当前不是有效 Git 仓库。
- 无法创建 worktree、查看 diff、提交设计或按 Task 创建 commit。
- 在 Git 恢复前，只通过最小范围 `apply_patch` 修改文件，并在本台账记录。

## 执行日志

### 2026-07-15：设计与计划

- 完成代码审查和 NVIDIA 通用适配边界确认。
- 用户确认 GPU 失败时允许 CPU 回退，但必须明确告警。
- 用户批准方案 B，并授权把确认事项集中到最后。
- 创建设计、实施计划和进度管理文件。

### 2026-07-15：Task 1 路径与工作目录

- 新增 `applications/inference/paths.py`，提供输入边界校验、独立任务目录和非空目录保护。
- `kml_roi_infer.py` 新增兼容参数 `--job_id`；未显式传入 `--work_dir` 时使用 `backend/runtime/inference_jobs/{uuid}`。
- `pipeline.py` 不再在启动时删除已存在的工作目录。
- 推理输出先写入任务 staging，再用同文件系统 `os.replace` 发布；FID 作为目录分量前显式拒绝路径穿越。
- RED：`python -m unittest test_inference_jobs.TestInferencePaths -v` 因 `paths.py` 不存在失败。
- GREEN：同命令 4/4 PASS；`py_compile` 通过。
- 未验证：`test_spectral_indices.py` 因宿主 Python 缺少 `rasterio` 无法导入，与本次路径修改无关。
- 回滚点：恢复 CLI 的固定 `--work_dir` 默认值以及 `pipeline.py` 的启动清理逻辑（不建议，会恢复并发互删风险）。

### 2026-07-15：Task 2 批量瓦片与状态

- `run_mmseg_tiles` 由逐瓦片 `execute(names=[tile])` 改为整批一次 `execute(names=file_names)`。
- `mmseg_inference_caller` 新增可选 `return_details=True`，旧调用方默认仍获得 URL 列表。
- 结果统一为 `input_name/output_name/mask_name/status/error`，部分失败不丢失已成功瓦片。
- `_parse_last_json` 无有效 JSON 时改为显式报错，不再返回伪成功。
- 流水线结果状态改为 `succeeded/partial_failed/failed`。
- RED：批量 runner 因不支持 caller 注入失败；调用器因不支持 `return_details` 失败；无 JSON 测试因未报错失败。
- GREEN：`python -m unittest test_inference_runner.py test_inference_jobs.py -v` 10/10 PASS；相关文件 `py_compile` PASS。
- 未验证：尚未在 MMSeg310 容器内用真实模型执行整批推理。
- 回滚点：恢复 `run_mmseg_tiles` 的逐片循环；会恢复重复加载模型的严重性能问题。

### 2026-07-15：Task 3 通用 NVIDIA 设备解析

- 新增 `applications/inference/device.py`，支持 `auto/cpu/cuda/cuda:N`。
- 选择逻辑只检查 Torch CUDA 可见性、设备编号、实际张量运算和可注入 smoke test；GPU 名称只用于展示。
- 稳定错误码包含 `GPU_NOT_VISIBLE`、`GPU_RUNTIME_UNAVAILABLE`、`GPU_ARCH_UNSUPPORTED`、`MMCV_CUDA_OP_UNAVAILABLE`、`CUDA_OUT_OF_MEMORY`、`GPU_INFERENCE_FAILED`。
- 新增 Worker 配置：`INFERENCE_ACCELERATOR`、`INFERENCE_CPU_FALLBACK`、`INFERENCE_GPU_DEVICE`、`INFERENCE_MAX_CONCURRENCY`、`INFERENCE_JOB_TIMEOUT_SECONDS`、`INFERENCE_KEEP_FAILED_WORKDIR`。
- RED：5 个设备场景因 `device.py` 不存在失败；配置测试因字段不存在失败。
- GREEN：`python -m unittest test_inference_device.py test_inference_runner.py test_inference_jobs.py -v` 16/16 PASS；`py_compile` PASS。
- 未验证：真实容器的 Torch/CUDA/MMCV/model smoke test 将在 Task 8/10 执行。
- 回滚点：删除设备解析模块和新环境变量；旧 CPU 调用不受影响。

### 2026-07-15：Task 4-5 持久化任务与 Worker

- 新增 `inference_jobs` 模型，记录状态、请求、结果、设备、降级、进度、取消、Worker 和时间字段。
- 任务状态机限定 `queued -> running|cancelled` 和 `running -> 终态`；数据库领取使用条件 update 避免重复领取。
- 新增 `/api/inference/jobs`、`/api/inference/jobs/{id}`、`/cancel` 和 `/capabilities`，全部复用现有 session 鉴权。
- 新增 `InferenceWorker` 和 `run_inference_worker.py`，MMSeg 拆为 `load_model` 与 `run_inference_with_model`，跨任务复用模型。
- 每个瓦片后更新进度并检查取消；CUDA OOM 时清理 GPU 模型、加载 CPU 模型并重跑当前任务。
- 单任务 `requested_device=cpu` 现已真正使用按需缓存的 CPU 模型，结束后恢复常驻 GPU 模型；Worker 重启会将同容器旧进程遗留任务标为 `WORKER_RESTARTED`，避免永久卡在 `running`。
- RED：状态转换、请求路径、模型复用、OOM 降级和取消测试均在实现前失败。
- GREEN：纯逻辑和 Worker 测试 PASS；新增 Python 文件 `py_compile` PASS。
- 未验证：2 个 Flask/SQLite API 集成测试因缺 Flask/rasterio 跳过；`pip install -r requirements.txt` 因当前网络 TLS 失败未能补齐环境；真实 MMSeg 模型尚未启动。
- 回滚点：可不启动 Worker/不注册新 Blueprint，旧同步 API 在 Task 6 改造前仍存在。

### 2026-07-15：Task 6-7 入口统一与双前端

- Node 新增统一的 Flask 推理后端代理；`/api/inference/kml-roi` 只创建异步任务，查询、取消和能力接口均透传 Flask。
- 删除 Node 入口中的 `child_process/execFile`、Python 解释器探测和直接推理分支，避免同一请求存在两条执行链路。
- Miner 与 GeoView 均改为“创建任务 -> 轮询终态”，默认设备为 `auto`，界面显示进度、实际设备和 CPU 回退原因。
- 验证：Miner 30/30 测试通过，`node --check` 通过，两个前端生产构建通过。
- 回滚点：前端可恢复旧提交接口；Node 代理与 Flask API 没有改变上传文件和结果文件的目录结构。

### 2026-07-15：Task 8 Docker 与 NVIDIA 通用运行时

- 基础 Compose 新增 CPU-safe Worker；统一 `INFERENCE_IMAGE` 同时承载 CPU/GPU 推理，`docker-compose.gpu.yml` 只为 Worker 申请 NVIDIA GPU。
- 新增运行时自检，报告 Torch/CUDA、MMCV/MMEngine/MMSeg、有效设备、compute capability 与回退原因；不按显卡型号分支。
- 旧运行镜像验证为 `torch 2.2.2+cpu`，即使容器成功注入 NVIDIA GPU，也会被正确判定为 `GPU_RUNTIME_UNAVAILABLE`。
- 真实模型基线进一步确认旧 Torch 2.2.2 无法加载当前 DINOv3 代码；因此 Worker 与 Web 镜像已解耦，CPU 回退也使用新的 Torch 2.7 统一推理镜像。
- 新增 `Dockerfile.inference-gpu`：PyTorch 2.7.0 + CUDA 12.8，并从源码构建带 CUDA ops 的 MMCV 2.1.0；架构列表覆盖多代 NVIDIA GPU，而非针对单一型号。
- 原 5.93 GB 训练 checkpoint 含 optimizer/message_hub；已在不覆盖原文件的前提下生成 1.97 GB 推理专用 checkpoint，并让入口优先选择它，降低加载内存峰值和离线包体积。
- Compose 合并配置验证通过。GPU 镜像构建经历 setuptools、依赖索引和旧 MMCV ABI 不匹配问题后已收敛。
- 多阶段镜像只从构建阶段复制 MMCV wheel，不把 CUDA devel 工具链带入运行层；正式标签内容摘要为 `sha256:7895038cdd5508c581d5399ccb808bfeaec85dab4b452590ca5fb029bda7fcd2`，本机体积约 14.45 GB。
- 验收机 NVIDIA 驱动 581.15、compute capability 12.0；真实 CUDA 张量、MMCV RoIAlign、模型加载、512×512 前向和 Worker 初始化全部通过。
- 回滚点：只使用基础 Compose 即恢复 CPU Worker；GPU 镜像和 override 均不改变任务 API。

### 2026-07-15：Task 9 运行安全

- Flask 保留 `HTTPException` 状态码，未知异常只记录服务端日志并返回通用 500，不再向客户端暴露堆栈。
- 生产后端改用 Gunicorn；数据库及管理员凭据必须通过环境变量提供，MySQL 不再默认暴露宿主端口。
- 下载与删除结果文件增加根目录边界检查，并补充路径穿越测试。
- 验证：凭据静态扫描、路径测试、Compose 配置和 Python 编译检查通过。
- 回滚点：Gunicorn 启动脚本和安全边界可分别回滚，不影响数据库结构。

### 2026-07-15：Task 10 文档与验收

- 新增 NVIDIA 通用兼容性、自检、错误码、CPU 回滚和版本验收说明；同步更新系统指南与离线部署指南。
- Docker `MMSeg310` 环境执行任务/API 集成测试通过。
- GPU 镜像内 CUDA/MMCV/model smoke、Worker 初始化和固定样例 CPU/GPU 一致性已通过。
- 推理 checkpoint SHA256 清单已生成；离线镜像 tar 尚未导出，因此不能伪造 tar SHA256，作为超大交付物选择留待最终确认。

### 2026-07-15：最终回归

- `geoview-runtime:gpu-cu128` 多阶段镜像构建成功：内容摘要 `sha256:7895038cdd5508c581d5399ccb808bfeaec85dab4b452590ca5fb029bda7fcd2`，本机体积 14,449,129,716 bytes。
- NVIDIA GPU 自检、MMCV RoIAlign、模型加载、512×512 前向与 Worker 初始化通过；同镜像无 GPU 运行时正确回退 CPU。
- 容器完整后端测试 66/66 PASS；Miner 测试 30/30 PASS；推理 Python 文件 `py_compile` 与 Node 入口语法检查 PASS。
- Miner 和 GeoView 生产构建 PASS；仅出现既有 bundle 体积、Browserslist 数据过期及 `/deep/` 弃用警告。
- 基础/GPU Compose 合并配置断言 PASS：基础 Worker 不申请 GPU，override 仅为 Worker 申请 `nvidia` 设备。
- 推理 checkpoint SHA256 复核 PASS：`132110DA8972F616C7B8308C2C892799EF94554D95C339CB88180305633E52AF`。

## 已确认与发布时输入

- Git 元数据暂不恢复。
- 不保留旧同步接口的 `wait=true` 行为。
- 生产环境的 `ADMIN_PASSWORD`、`SECRET_KEY`、`MYSQL_PASSWORD`、`MYSQL_ROOT_PASSWORD` 由部署方提供，仓库不保存默认明文。
- 是否导出离线 tar 延后到轻量镜像验收后决定，避免为即将替换的旧镜像生成超大交付物。

## 2026-07-16：轻量镜像与单任务性能阶段

- 用户确认暂不恢复 Git 元数据。
- 用户确认异步任务模式覆盖全部工作后，不保留旧同步等待行为；GeoView 迁移到规范任务接口后删除旧路由别名。
- 已澄清当前推理镜像不是完整离线项目包；完整交付还必须包含 Web/Node、MySQL、模型、Compose、配置和业务数据。
- 用户确认优先目标为“单个大任务尽快完成”，不以同一 GPU 多模型并发作为优化方向。
- GPU 预热微基准：batch 1/2/4/8 分别为 2.890/2.972/2.872/1.506 张每秒；默认继续使用 batch=1，batch=2 只作为目标 ROI 的候选。
- 当前镜像内容尺寸约 14.45 GB，Docker 展开尺寸 35.9 GB；确认存在旧 checkpoint、重复 Torch 层和无关应用资源。
- 用户批准“三级镜像层 + 独立模型卷 + 单容器有界流水线”设计。
- 设计文档：`docs/superpowers/specs/2026-07-16-inference-runtime-pipeline-design.md`。
- 镜像实施计划：`docs/superpowers/plans/2026-07-16-lightweight-inference-image-implementation.md`。
- 性能实施计划：`docs/superpowers/plans/2026-07-16-single-task-inference-pipeline-implementation.md`。
- 两份计划已通过需求覆盖、占位符和类型一致性自审；按顺序先验收轻量镜像，再建立固定基线实施流水线优化。
- 轻量镜像 Task 1 完成：生产 Worker 强制 `model.inference.pth`，开发 CLI 仅在训练 checkpoint 实际存在时兼容回退；双缺失错误列出两条候选路径。
- Task 1 TDD/评审：新增测试能捕获旧行为与错误 Worker 接线；需求复审通过、代码质量复审 Approved；主线程 48 项验证为 43 PASS、5 个宿主依赖 SKIP。
- 当前 Docker Desktop daemon 未运行，Task 1 容器完整依赖回归延后到镜像构建前补跑。
- Docker Desktop 已恢复；Task 1 容器完整依赖回归补跑 48/48 PASS。
- 轻量镜像 Task 2 完成：新增 Worker-only Flask/SQLAlchemy 工厂，并将 `applications`、`extensions`、`models` 的 Web/模型初始化改为兼容的惰性边界。
- Task 2 真实冷启动仅导入推理数据库依赖并只创建 `inference_jobs`、`inference_worker_states`；完整 Web app 仍注册 7 个业务 API blueprint 与 `_uploads`。
- Task 2 评审循环修复了父包 eager import 和 `init_upload/init_dotenv` 导出兼容问题；最终需求复审通过、质量评审 Approved。
- 主线程新鲜验证：Task 2 聚焦组合 50/50 PASS，容器完整后端 75/75 PASS，相关文件 `py_compile` PASS；仅保留既有依赖弃用警告。
- 轻量镜像 Task 3 完成：新增 core/worker 依赖约束、项目根 allowlist 构建上下文和无 Docker daemon 的路径契约测试。
- Task 3 评审循环修复了 Paddle/ONNX/其他权重、Zone.Identifier、vendored `.git`、notebook、临时输出及 `.gz` 大资源泄漏，并修正测试 evaluator 的根路径锚定语义。
- Task 3 最终审计：工作区 100 个大于 1 MiB 的文件全部被专用上下文排除，CUGRS/MMseg/DINOv3 运行源码与配置保留；需求复审通过、质量评审 Approved。
- 主线程新鲜验证：契约测试 5/5 PASS；`docker build --check -f docker/Dockerfile.inference-gpu .` PASS，仅提示无 Git commit metadata。
- 轻量镜像 Task 4 实现与静态评审完成：镜像拆分为 `inference-base`、`inference-core`、`inference-worker`，使用 Python 3.10 `/opt/venv`、Torch 2.7.0+cu128 和按完整 ABI 指纹缓存的 MMCV 2.1.0 wheel；最终 Worker 采用非 root 启动及源码白名单。
- Task 4 依赖兼容修正：`inference-core` 锁定官方 `mmsegmentation==1.2.2`（`MMCV_MAX=2.2.0`，兼容 MMCV 2.1.0）；最终 Worker 通过 `PYTHONPATH` 优先使用仓库 vendored MMSeg 1.1.2，其运行上限已放宽为 `<2.2.0`，因此 Worker 镜像自检仍报告 1.1.2。
- Task 4 评审循环修复了坏 MMCV 缓存污染、DINOv3 eager eval/缺失 backbone 权重预加载、空目录与符号链接资产漏检，以及构建 checker 未使用 vendored MMSeg 等问题；最终规格复审 PASS、质量复审 Approved。
- 主线程新鲜静态验证：Task 4 契约 44/44 PASS，相关 Python/Shell 语法 PASS，`docker build --check` PASS。
- Task 4 真实构建：`inference-base` 和 `inference-core` 已完成；core 容器自检为 Torch 2.7.0+cu128、CUDA 12.8、MMCV 2.1.0 CUDA ops、MMEngine 0.10.4、官方 MMSeg 1.2.2，`nvcc=false`、`forbidden_assets=[]`。
- `inference-worker` 的所有代码层与 final checker 已通过（vendored MMSeg 1.1.2、`nvcc=false`、`forbidden_assets=[]`），但 Docker Desktop 在最后导出镜像时因 C: 可用空间为 0 崩溃并返回 RPC EOF；Task 4 仍未标记完成，待清理或迁移 Docker 162,944,516,096-byte 数据盘后补做镜像落盘和独立容器内容检查。
- 轻量镜像 Task 5 完成：生产 Worker 不再 bind 宿主 `/app/backend` 或 `/app/docker`，仅显式只读挂载配置、推理 checkpoint、栅格输入与 KML；开发 override 使用 `!override` 显式恢复 backend 源码及必要数据/运行卷，不隐式继承 checkpoint、raster 或 docker 子挂载。
- Task 5 运行时自检新增 `image_build`、固定 `checkpoint_path`、字节数和 8 MiB 分块 SHA256；缺模型稳定返回 `MODEL_CHECKPOINT_MISSING`。契约 49/49 PASS，prod/GPU/dev 三组 Compose 离线展开 PASS，规格复审 PASS、质量复审 Approved。
- 轻量镜像 Task 6 完成：新增 PowerShell/Bash 可复现构建 wrapper，仅在 candidate 的内容、CPU runtime、镜像 ID/尺寸全部验证后才赋予独立 canonical `geoview-inference-worker:current`；旧 `geoview-runtime:gpu-cu128` 不被覆盖，等价 tag 在任何 Docker 调用前拒绝。
- Task 6 离线清单工具对 7 个必需 artifact 以及可选 maps/volumes 记录规范相对路径、字节数和小写 SHA256；已拒绝路径穿越、越界/大小写别名输出、symlink、自包含、重复 key 和非原子落盘。fake Docker 行为测试覆盖 Bash/PowerShell 各失败阶段均不打 canonical tag。
- Task 6 最终验证：契约 74/74 PASS，1 项 Linux 专用测试按 Windows 平台预期 SKIP；PowerShell parser、`bash -n`、`py_compile` 与三组 Compose 均 PASS；规格复审 PASS、质量复审 Approved。未执行真实 build/tag/save/cleanup。
- 2026-07-16 用户取消轻量/拆分镜像路线：继续使用已验证 `geoview-runtime:gpu-cu128`，不再构建、迁移或发布 `geoview-inference-*`。
- 已删除本次 4 个 `geoview-inference-*` 镜像标签和 2 个 CUDA 构建标签，未使用 BuildKit 缓存由 79.58 GB 降为 0；旧 GPU/Web/MySQL/江西镜像、容器和 volumes 未删除。
- Docker ext4 执行 TRIM 回收 81.5 GiB，原 C 盘 VHD 经 DiskPart compact 从约 156.25 GiB 降为 60.20 GiB，C 盘可用空间从约 2.4 GB 恢复到 98.45 GB；D 盘未保留迁移副本。
- 生产 Worker 已恢复 `./backend:/app/backend:ro` 和 `./docker:/app/docker:ro`，`image_bundle.env` 恢复 `INFERENCE_IMAGE=geoview-runtime:gpu-cu128`；轻量镜像实验文件仅作历史记录，不得用于当前部署。
