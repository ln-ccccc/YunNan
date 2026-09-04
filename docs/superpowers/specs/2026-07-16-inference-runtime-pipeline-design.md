# 轻量推理运行时与单任务流水线设计

日期：2026-07-16  
状态：用户已批准  
前置设计：`2026-07-15-nvidia-inference-worker-design.md`

## 1. 目标

本次改造只解决两个直接目标：

1. 缩小 NVIDIA 推理镜像，移除旧模型、重复 Torch 层、前端、Node、训练资源和无关依赖；
2. 缩短单个大型 KML ROI 任务的端到端耗时，同时保持任务 API、输出格式、结果目录和 CPU 回退行为兼容。

优先级为：结果正确性与可恢复性 > 单任务总耗时 > 镜像体积 > 构建耗时。

## 2. 已测基线

当前 `geoview-runtime:gpu-cu128` 存在两种尺寸口径：

- Docker 内容尺寸：14,449,129,716 bytes；
- `docker image ls` 展开/虚拟尺寸：35.9 GB。

主要体积来源：

- 基础镜像中保留旧的 5.6 GB `model.pth`；
- Torch CUDA 安装形成约 6.76 GB 新层，旧环境仍保留在父层；
- 当前 Conda 目录约 8.2 GB，其中 Torch 约 2.0 GB、NVIDIA Python 运行库约 3.6 GB；
- 镜像包含与 Worker 无关的 frontend、miner、训练代码和训练资源。

当前 8 GB NVIDIA GPU、512×512 输入、预热后微基准：

| Batch | 吞吐 | 峰值 allocated 显存 | 峰值 reserved 显存 |
| --- | ---: | ---: | ---: |
| 1 | 2.890 张/秒 | 2.264 GiB | 2.541 GiB |
| 2 | 2.972 张/秒 | 2.644 GiB | 3.162 GiB |
| 4 | 2.872 张/秒 | 3.277 GiB | 4.266 GiB |
| 8 | 1.506 张/秒 | 4.671 GiB | 6.633 GiB |

因此默认保持单 GPU 推理流和 batch=1。batch=2 只作为目标数据集上的可测候选，不以合成微基准的约 2.8% 提升为由默认启用。

## 3. 非目标

- 不把预处理、GPU 推理、后处理拆成网络微服务；
- 不在一张 GPU 上同时加载多个模型副本；
- 不为某个 NVIDIA 显卡名称编写分支；
- 不引入 TensorRT、模型量化或新任务队列产品；
- 不改变现有结果文件命名、任务状态和输出目录结构；
- 不顺带重构 Web、Miner、光谱指数或其他无关功能。

## 4. 镜像架构

使用一个 Dockerfile 的三个可命名 target，最终仍运行一个 Worker 容器。

### 4.1 `inference-base`

职责：提供最小操作系统、Python 3.10 和地理影像运行库。

内容：

- `nvidia/cuda:12.8.0-base-ubuntu22.04`；
- Python 3.10 与独立虚拟环境；
- GDAL/rasterio、OpenCV 所需系统动态库；
- 非 root Worker 用户和固定工作目录。

不包含：Conda、Torch、项目代码、模型、Node、前端和编译工具链。

### 4.2 `inference-core`

职责：提供可独立验证的通用 NVIDIA 推理依赖。

内容：

- Torch 2.7.0+cu128、torchvision 0.22.0；
- MMCV 2.1.0 CUDA ops、MMEngine 0.10.4、官方 MMSeg 1.2.2（其 `MMCV_MAX=2.2.0`，覆盖当前 MMCV 2.1.0）；
- Worker 实际需要的 NumPy、OpenCV、rasterio、GDAL、Shapely 等依赖；
- 版本清单、镜像标签和不依赖模型的 CUDA/MMCV 自检入口。

MMCV 在 CUDA devel builder 阶段编译，只把 wheel 复制到运行阶段。最终层不得包含 `nvcc`、CUDA headers、编译缓存或源码构建目录。缓存 wheel 必须绑定 Python、Torch、CUDA、MMCV 和目标架构组合，不能只按 `mmcv-2.1.0` 文件名盲目复用。

### 4.3 `inference-worker`

职责：提供生产 Worker 入口和当前项目推理代码。

只复制：

- Flask 应用初始化、任务模型和数据库访问；
- `applications/inference`；
- `applications/kml_roi`；
- MMSeg 调用器和运行所需模型定义；
- 配置文件及这些模块的直接依赖。

明确排除：

- `model.pth`、`model.inference.pth` 和预训练 backbone 权重；
- vendored 仓库的 `.git`、docs、tests、demo、results 和训练数据；
- frontend、miner、Node modules、构建产物和静态业务数据；
- notebook、训练脚本及非运行时 wheel。

生产 Compose 不再把整个 `./backend` 覆盖挂载到 Worker。开发环境可通过单独 override 绑定源码，生产镜像保持代码不可变。

`inference-core` 安装官方 `mmsegmentation==1.2.2` 以闭合依赖解析和 builder ABI 校验；最终 Worker 的 `PYTHONPATH` 优先指向仓库 vendored MMSeg 1.1.2。该 vendored 版本的 `MMCV_MAX` 为 `2.2.0`（运行约束 `<2.2.0`），因此继续兼容镜像内 MMCV 2.1.0。镜像自检在 Worker 视图中仍应报告 MMSeg 1.1.2。

### 4.4 独立模型卷

生产环境把：

`backend/model/mmseg_config/model.inference.pth`

只读挂载到镜像中现有 checkpoint 路径。模型文件独立记录尺寸和 SHA256，允许不重建运行镜像直接替换模型。原 5.6 GB 训练 checkpoint 不进入生产离线包。

若推理专用 checkpoint 缺失，生产 Worker 启动失败并返回明确的 `MODEL_CHECKPOINT_MISSING`，不再静默依赖镜像父层中的旧模型。开发 CLI 可继续保留对原 checkpoint 的兼容回退。

## 5. 依赖边界与调试

依赖按用途锁定为三份清单：

- `inference-system`：APT 动态库及版本；
- `inference-core`：Torch、CUDA Python 库、MMCV/MMEngine/MMSeg；
- `inference-worker`：Flask/SQLAlchemy/MySQL、KML/GeoTIFF 和图像处理依赖。

每一层提供独立检查：

1. base：Python、GDAL、rasterio、OpenCV import；
2. core：Torch CUDA 版本、真实 CUDA 张量、MMCV RoIAlign；
3. worker：数据库配置、模型文件、模型加载和 512×512 前向。

最终自检 JSON 增加镜像构建版本、模型 SHA256、各阶段耗时和失败阶段。日志继续使用稳定错误码，不输出凭据或完整客户端路径。

## 6. 单任务流水线

运行时仍只有一个任务处于 GPU 执行状态。一个任务内部按 FID 形成有界流水线：

```text
KML/范围过滤
    -> CPU 裁剪与 PNG 准备
    -> 有界 prepared queue
    -> 单模型 GPU 推理
    -> 有界 predicted queue
    -> CPU 裁边、统计和 staging
    -> 原子发布
```

### 6.1 任务单元

一个 FID 及其单年或双年变体是最小完整单元。预处理结果包含 FID、年份、tile 路径、几何和目标命名。只有该 FID 所需变体全部推理成功后，才进入统计与发布阶段。

### 6.2 CPU 并行

- 预处理使用小型有界进程池，默认候选为 2 个进程；每个进程独立打开 raster，避免跨进程共享 GDAL dataset；
- 后处理使用独立小型有界执行器；
- 队列容量有限，不能随 FID 数量无限累积 PNG、NumPy 数组或预测结果；
- 实际默认进程数必须通过目标大图端到端基准确定，不能只根据 CPU 核数放大。

### 6.3 GPU 推理

- 模型只加载一次，只在主 Worker 进程访问；
- 默认 FP32、batch=1；
- batch=2 仅在真实 ROI 基准同时提高端到端吞吐且不增加失败率时启用；
- FP16 autocast 为实验候选，只有通过结果一致性和稳定性门槛后才可成为显式配置，不能静默改变精度；
- CUDA OOM 时先缩回 batch=1；仍失败时才沿用现有 CPU 回退策略。

### 6.4 取消、超时和错误

- 领取任务、提交预处理、每次 GPU 前向、提交后处理和发布前均检查取消与超时；
- 首个失败停止继续提交新单元，已运行子任务收敛后统一清理；
- 每个 FID 保留结构化失败原因，部分成功仍使用 `partial_failed`；
- 所有输出先进入任务 staging，只有完整 FID 才使用 `os.replace` 发布；
- 同一输出根目录和 FID 的发布使用进程间锁，防止未来多 GPU Worker 互相覆盖。

## 7. 阶段计时与优化顺序

先测量再并行。任务结果增加兼容性的可选字段 `stage_timings`：

- `load_features_seconds`；
- `prepare_tiles_seconds`；
- `gpu_inference_seconds`；
- `postprocess_seconds`；
- `publish_seconds`；
- `total_seconds`；
- 处理 FID/瓦片数和有效吞吐。

执行顺序：

1. 在未改变数据流前记录真实大任务基线；
2. 引入有界预处理并发并复测；
3. 引入预处理/GPU/后处理重叠并复测；
4. 仅在前述结果稳定后评估 batch=2；
5. 最后独立评估 FP16，不将其与流水线改造混在同一次结论中。

任何一步未提升目标大任务总耗时，默认不保留该优化。

## 8. API 与兼容性

- 保持 `/api/inference/jobs` 创建、查询、取消和能力接口；
- Miner 与 GeoView 都迁移到规范异步任务接口；
- 删除旧的阻塞等待行为和 `wait=true` 设计；
- GeoView 迁移完成后删除 `/api/analysis/kml_roi_inference` 旧路由别名；
- 保持任务 JSON 既有字段，新增诊断字段均为可选；
- 不改变历史结果查询与输出文件 URL。

## 9. 离线交付

`geoview-inference-worker` 只代表推理环境，不代表完整项目。完整离线包清单至少包含：

- Web/Node 应用镜像 tar；
- 推理 Worker 镜像 tar；
- MySQL 镜像 tar；
- `model.inference.pth` 与 SHA256；
- Compose、配置模板、启动与自检脚本；
- 离线地图、KML、Excel 等部署所需业务数据；
- 可选数据库和命名卷备份。

清单记录每个文件的 SHA256、字节数、镜像内容摘要和运行版本。打包脚本在输出前验证全部必需文件存在，不把真实密码写入包中。

## 10. 验收标准

### 10.1 镜像

- 最终镜像不包含 `nvcc`、旧 `model.pth`、frontend、miner、Node modules、训练数据和预训练 backbone 文件；
- `docker image ls` 展开尺寸目标不超过 15 GB；若上游二进制依赖导致无法达到，硬性上限为当前 35.9 GB 的 50%，并记录最大目录；
- 冷启动自检、GPU Worker 初始化和无 GPU CPU 回退全部通过；
- 从空构建缓存可复现，缓存命中不能绕过 ABI 自检。

### 10.2 正确性

- 现有后端、Miner、前端和 Compose 验证不退化；
- 同一黄金 ROI 的 FP32 新旧流水线输出应完全一致；
- 若启用 FP16，像素一致率至少 99.99%，且各类别面积占比绝对差不超过 0.02 个百分点；
- 取消、超时、OOM CPU 回退、部分失败和 Worker 重启恢复测试通过。

### 10.3 性能

- 以同一目标大图、KML、FID 数量和暖机条件比较端到端耗时；
- 新流水线不得慢于基线；目标为单个大任务总耗时下降至少 20%；
- 分别报告预处理、GPU、后处理和发布耗时，不用单张合成图片吞吐代替端到端结论；
- 峰值主机内存、GPU 显存和临时磁盘不得随 FID 总数无界增长。

## 11. 回滚

- 保留当前已验证镜像标签和摘要，不覆盖后立即删除；
- 通过 `INFERENCE_IMAGE` 切回当前镜像；
- 流水线保留串行执行实现作为一次发布周期内的显式回滚开关；
- FP16 和 batch=2 分别独立关闭；
- 数据库结构、任务 API 和结果目录无需回滚。

## 12. 实施边界

实施拆为两个可独立回滚阶段：

1. 先完成轻量镜像、独立模型卷、离线清单和逐层自检；
2. 再完成阶段计时、目标大任务基线和有界流水线优化。

两阶段不得混成一个不可归因的性能结论。只有第一阶段镜像验收通过后，第二阶段才以新镜像作为基准环境。
