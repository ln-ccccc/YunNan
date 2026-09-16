# NVIDIA 通用推理兼容与验收

> 命名说明：云南推理镜像统一为 `yunnan-inference-worker:current`（由
> `docker/build-inference-image.ps1/.sh` 构建晋升）。历史文档中的旧称
> `geoview-runtime:gpu-cu128` 即同一镜像血统；云南镜像名一律
> 含 `yunnan`，与江西镜像严格区分。

## 1. 适配边界

系统不按 5060 或其他具体显卡型号分支。Worker 启动时依次检查：

1. 容器内 PyTorch CUDA 运行时；
2. NVIDIA GPU 可见性和设备编号；
3. 真实 CUDA 张量运算；
4. MMCV/MMSeg 模型加载 smoke test；
5. 瓦片推理。

GPU 名称和 compute capability 只用于展示和验收记录，不参与设备选择。

## 2. 运行模式

CPU 强制模式：

```bash
export INFERENCE_IMAGE=yunnan-inference-worker:current
export INFERENCE_ACCELERATOR=cpu
docker compose -f docker-compose.prod.yml up -d
```

`INFERENCE_IMAGE` 是统一推理运行时：同一个 Torch 2.7.0/CUDA 12.8/MMCV 2.1.0 镜像既可在无 GPU 授权时执行 CPU 推理，也可在 GPU override 下执行 CUDA 推理。它与 Web 使用的 `APP_IMAGE` 分离，避免旧 Web 镜像中的 Torch 版本阻断 DINOv3 模型加载。

自动模式（基础 Compose 不申请 GPU，因此会显式回退 CPU）：

```bash
export INFERENCE_ACCELERATOR=auto
export INFERENCE_CPU_FALLBACK=true
docker compose -f docker-compose.prod.yml up -d
```

NVIDIA GPU 模式：

```bash
docker build -f docker/Dockerfile.inference-gpu \
  -t yunnan-inference-worker:current docker
export INFERENCE_IMAGE=yunnan-inference-worker:current
export INFERENCE_ACCELERATOR=auto
export INFERENCE_CPU_FALLBACK=true
docker compose -f docker-compose.prod.yml -f docker-compose.gpu.yml up -d
```

GPU override 只向 `inference-worker` 分配 GPU，Flask Web、Miner Node 和前端不获得 GPU 权限。
构建参数不是按显卡型号选择：镜像通过 CUDA capability、真实张量运算、MMCV CUDA ops 和模型加载逐级验证。当前 MMCV 架构列表覆盖 SM 7.5、8.0、8.6、8.9、9.0、10.0，并为 SM 12.0 保留 PTX；它面向受 PyTorch 2.7/CUDA 12.8 支持的现代 NVIDIA GPU，不承诺支持已被上游运行时淘汰的老架构。宿主机仍需安装与 CUDA 12.8 容器兼容的 NVIDIA 驱动及 NVIDIA Container Toolkit。

## 3. 运行时自检

```bash
docker exec cugrs-inference-worker \
  python /app/docker/check-inference-runtime.py
```

命令输出一行 JSON，至少审核：

- `torch/mmcv/mmengine/mmseg` 版本；
- `requested_device/effective_device`；
- `gpu_name/compute_capability`；
- `fallback_reason/warnings`；
- `ok`。

`/api/inference/capabilities` 读取 Worker 写入 MySQL 的实际探测结果，不在无 GPU 权限的 Web 容器中误探测。

## 4. 稳定错误码

| 错误码 | 含义 |
| --- | --- |
| `GPU_NOT_VISIBLE` | Worker 容器看不到 NVIDIA GPU |
| `GPU_RUNTIME_UNAVAILABLE` | PyTorch CUDA 运行时不可用 |
| `GPU_ARCH_UNSUPPORTED` | 当前 Torch/CUDA 构建不支持 GPU 计算架构 |
| `MMCV_CUDA_OP_UNAVAILABLE` | MMCV CUDA 算子缺失或不匹配 |
| `MODEL_GPU_LOAD_FAILED` | 模型无法在 GPU 加载 |
| `CUDA_OUT_OF_MEMORY` | GPU 显存不足，允许时用 CPU 重跑 |
| `GPU_INFERENCE_FAILED` | 其他 GPU 推理失败 |
| `WORKER_RESTARTED` | Worker 进程重启，上一进程中的运行任务已明确置为失败 |
| `JOB_TIMEOUT` | 任务在瓦片边界检查时超过配置时限 |

只要 `INFERENCE_CPU_FALLBACK=true`，GPU 探测/加载失败会回退 CPU；任务、API 和界面必须同时显示 `effective_device`、`fallback_reason` 和 `warnings`。
同一容器中的 Worker 进程重启时，无法安全续跑的旧 `running` 任务不会永久悬挂，而会以 `WORKER_RESTARTED` 结束；新的排队任务随后继续领取。

## 5. 版本和离线包

禁止只根据显卡名称选择 Torch/CUDA/MMCV 版本。最终版本组合必须在目标 NVIDIA 机器上通过：

1. 运行时自检；
2. CUGRS 模型 GPU 加载；
3. 固定 ROI GPU 推理；
4. 同 ROI CPU/GPU 结果对比。

通过后在离线发布清单记录镜像 SHA256、NVIDIA 驱动范围、Torch/CUDA/MMCV/MMEngine/MMSeg 版本与模型权重 SHA256。

当前已验证发布候选为：

| 项目 | 值 |
| --- | --- |
| 镜像 | `yunnan-inference-worker:current` |
| 镜像内容摘要 | `sha256:7895038cdd5508c581d5399ccb808bfeaec85dab4b452590ca5fb029bda7fcd2` |
| 本机构建体积 | 14,449,129,716 bytes（约 14.45 GB） |
| Python / Torch / CUDA | 3.10.20 / 2.7.0+cu128 / 12.8 |
| MMCV / MMEngine / MMSeg | 2.1.0 / 0.10.4 / 1.1.2 |
| 验收宿主驱动 | 581.15 |
| GPU 验收 | CUDA 张量、MMCV RoIAlign、模型加载、512×512 前向和 Worker 初始化均通过 |
| 无 GPU 验收 | `auto` 回退 CPU，稳定原因码为 `GPU_NOT_VISIBLE` |
| CPU/GPU 黄金样例 | 241,081 像素中仅 4 像素不同，一致率 99.99834%，类别计数一致 |

镜像使用多阶段构建，CUDA devel 工具链仅存在于 MMCV 构建阶段，不进入最终运行镜像。上述“镜像内容摘要”用于确认本地镜像内容；离线 tar 的 SHA256 必须在实际执行 `docker save` 后另行生成，二者不能互相替代。

当前推理入口优先使用 `backend/model/mmseg_config/model.inference.pth`。该文件仅保留原训练 checkpoint 的 `meta` 与 `state_dict`，不覆盖 `model.pth`；体积由 5.93 GB 降至 1.97 GB，SHA256 为 `132110DA8972F616C7B8308C2C892799EF94554D95C339CB88180305633E52AF`。若推理专用文件缺失，代码仍兼容回退到原 checkpoint。

GPU 镜像离线导出与加载：

```bash
docker save yunnan-inference-worker:current -o offline_bundle/images/yunnan_inference_worker_current.tar
sha256sum offline_bundle/images/yunnan_inference_worker_current.tar \
  > offline_bundle/images/yunnan_inference_worker_current.tar.sha256

docker load -i offline_bundle/images/yunnan_inference_worker_current.tar
export INFERENCE_IMAGE=yunnan-inference-worker:current
```

## 6. 回滚

```bash
export INFERENCE_ACCELERATOR=cpu
docker compose -f docker-compose.prod.yml up -d --force-recreate inference-worker
```

不加载 `docker-compose.gpu.yml` 即可撤销 GPU 申请，任务 API、数据表和结果目录不变。
