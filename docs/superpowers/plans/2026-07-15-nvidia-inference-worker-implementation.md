# NVIDIA 通用推理 Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将当前 CPU 默认、双入口、逐瓦片重复加载的解译链路改造成支持通用 NVIDIA CUDA 检测、显式 CPU 降级、持久化任务和单模型常驻的统一推理 Worker。

**Architecture:** Flask 负责创建和查询推理任务，MySQL 保存任务状态，独立 Python Worker 串行领取任务并复用已加载的 MMSeg 模型。Miner Node API 保留原路径但代理 Flask；CPU 基础 Compose 保持可启动，GPU override 只向 Worker 分配 NVIDIA GPU。

**Tech Stack:** Python 3.10, Flask 2.2, Flask-SQLAlchemy, MySQL 8, PyTorch, MMCV, MMEngine, MMSegmentation, rasterio, Node 20, Express 5, Vue 2/3, Docker Compose

---

## File map

### New backend files

- `backend/applications/inference/device.py`: 设备配置、CUDA 检测、错误码和 CPU 降级。
- `backend/applications/inference/jobs.py`: 任务创建、序列化、领取、进度和状态转换。
- `backend/applications/inference/paths.py`: 输入根目录和输出路径边界校验、任务工作目录。
- `backend/applications/inference/worker.py`: 常驻模型 Worker 与任务循环。
- `backend/applications/api/inference.py`: 规范任务 API 和 capabilities API。
- `backend/applications/models/inference_job.py`: `inference_jobs` 数据模型。
- `backend/test_inference_device.py`: 设备解析测试。
- `backend/test_inference_jobs.py`: 任务状态、API、路径和领取测试。
- `backend/test_inference_runner.py`: 批量推理与模型复用测试。
- `backend/run_inference_worker.py`: Worker 容器启动入口。
- `docker-compose.gpu.yml`: NVIDIA GPU override。
- `docker/start-inference-worker.sh`: Worker 启动脚本。
- `docker/check-inference-runtime.py`: 容器运行时自检。
- `miner/services/inferenceBackend.js`: Miner 到 Flask 推理 API 的代理客户端。
- `miner/test/inferenceBackend.test.js`: Node 代理测试。

### Modified files

- `backend/applications/models/__init__.py`: 注册推理任务模型。
- `backend/applications/api/__init__.py`: 注册推理 Blueprint。
- `backend/applications/configs/config.py`: 推理环境变量和根目录配置。
- `backend/applications/kml_roi/inference_runner.py`: 从逐瓦片调用改为批量调用。
- `backend/applications/kml_roi/pipeline.py`: 独立工作目录、状态语义和可注入 runner。
- `backend/applications/kml_roi/service.py`: 去除伪成功、复用任务服务。
- `backend/applications/interface/mmseg_inference_caller.py`: 结构化批量结果和设备参数规范化。
- `backend/applications/interface/mmseg_segmentation.py`: 支持复用已初始化模型。
- `backend/applications/api/analysis.py`: 旧 API 兼容包装和路径安全。
- `backend/app.py`: 正确 HTTP 异常与 500 语义。
- `miner/server.js`: 删除直接 `execFile` 推理，改成 Flask 代理。
- `miner/src/composables/useMineData.js`: 提交任务并轮询。
- `miner/src/components/InferenceModal.vue`: 自动/CPU 选项、进度和降级提示。
- `frontend/src/api/upload.js`: 增加任务查询 API。
- `frontend/src/utils/getUploadImg.js`: 删除 `Promise.all` 和硬编码 CPU。
- `docker-compose.prod.yml`: 增加 Worker、共享结果卷和推理配置。
- `backend/requirements.txt`: 锁定新增运行依赖，保留 CPU 可用性。
- `backend/requirements-hf.txt`: 锁定 Torch/OpenMMLab 组合，不再只写安装注释。
- `docs/offline_deployment_guide.md`: CPU/GPU 离线部署、检查和回滚。
- `docs/system_guide.md`: 任务 Worker、端口和状态说明。

## Task 1: 推理路径边界和独立工作目录

**Files:**
- Create: `backend/applications/inference/__init__.py`
- Create: `backend/applications/inference/paths.py`
- Test: `backend/test_inference_jobs.py`
- Modify: `backend/applications/kml_roi/pipeline.py`
- Modify: `backend/kml_roi_infer.py`

- [ ] **Step 1: 写路径边界失败测试**

```python
class TestInferencePaths(unittest.TestCase):
    def test_resolve_input_rejects_path_outside_allowed_roots(self):
        with self.assertRaises(ValueError):
            resolve_input_path("/etc/passwd", [Path(self.temp_dir)])

    def test_create_job_workdir_is_unique(self):
        first = create_job_workdir(Path(self.temp_dir), "job-a")
        second = create_job_workdir(Path(self.temp_dir), "job-b")
        self.assertNotEqual(first, second)
        self.assertTrue(first.is_dir())
        self.assertTrue(second.is_dir())
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m unittest test_inference_jobs.TestInferencePaths -v`

Expected: FAIL，提示 `applications.inference.paths` 不存在。

- [ ] **Step 3: 实现最小路径模块**

```python
from pathlib import Path


def _is_within(path_obj, root_obj):
    try:
        path_obj.relative_to(root_obj)
        return True
    except ValueError:
        return False


def resolve_input_path(value, allowed_roots):
    path_obj = Path(value).expanduser().resolve()
    roots = [Path(root).expanduser().resolve() for root in allowed_roots]
    if not path_obj.exists():
        raise FileNotFoundError(f"输入文件不存在: {path_obj}")
    if not any(_is_within(path_obj, root) for root in roots):
        raise ValueError(f"输入路径不在允许目录中: {path_obj}")
    return path_obj


def create_job_workdir(runtime_root, job_id):
    root = Path(runtime_root).expanduser().resolve()
    path_obj = (root / str(job_id)).resolve()
    if not _is_within(path_obj, root):
        raise ValueError("任务编号非法")
    path_obj.mkdir(parents=True, exist_ok=False)
    return path_obj
```

- [ ] **Step 4: 将 CLI 工作目录改成任务唯一目录**

给 `kml_roi_infer.py` 增加 `--job_id`，默认使用 `uuid.uuid4().hex`；默认工作目录改为 `backend/runtime/inference_jobs/{job_id}`。`pipeline.py` 不再删除共享目录，只清理传入的当前任务目录。

- [ ] **Step 5: 运行路径和现有 KML 测试**

Run: `cd backend && python -m unittest test_inference_jobs.py test_spectral_indices.py -v`

Expected: 新增测试 PASS；现有 KML/KML 合并测试 PASS。

## Task 2: 批量执行瓦片并修正结果状态

**Files:**
- Test: `backend/test_inference_runner.py`
- Modify: `backend/applications/kml_roi/inference_runner.py`
- Modify: `backend/applications/interface/mmseg_inference_caller.py`
- Modify: `backend/applications/kml_roi/pipeline.py`
- Modify: `backend/applications/kml_roi/service.py`

- [ ] **Step 1: 写单次批量调用测试**

```python
def test_run_mmseg_tiles_calls_execute_once_for_all_files(self):
    caller = Mock()
    caller.execute.return_value = {
        "results": [
            {"input_name": "a.png", "status": "success"},
            {"input_name": "b.png", "status": "error", "error": "bad tile"},
        ]
    }
    failed, errors = run_mmseg_tiles(
        model_id="cc-ln/CUGRS",
        data_path="tiles",
        out_dir="out",
        file_names=["a.png", "b.png"],
        device="cpu",
        caller=caller,
    )
    caller.execute.assert_called_once()
    self.assertEqual(failed, ["b.png"])
    self.assertEqual(errors["b.png"], "bad tile")
```

- [ ] **Step 2: 运行测试确认当前实现失败**

Run: `cd backend && python -m unittest test_inference_runner.py -v`

Expected: FAIL，因为当前函数逐瓦片调用且不支持注入 caller。

- [ ] **Step 3: 实现结构化批量结果**

`mmseg_inference_caller.execute` 返回：

```python
{
    "status": "completed",
    "results": [
        {
            "input_name": name,
            "output_name": output_name,
            "mask_name": mask_name,
            "status": "success",
            "error": None,
        }
    ],
}
```

`run_mmseg_tiles` 一次传入全部 `file_names`，再从结构化结果生成 `failed_tiles` 和 `tile_errors`。

- [ ] **Step 4: 修正任务状态**

`pipeline.py` 按下列规则返回：

```python
if failed_tiles and written_fids:
    status = "partial_failed"
elif failed_tiles and not written_fids:
    status = "failed"
else:
    status = "succeeded"
```

`service._parse_last_json` 找不到有效 JSON 时抛出 `RuntimeError("推理进程未返回有效 JSON")`。

- [ ] **Step 5: 运行新增测试和 Python 语法检查**

Run: `cd backend && python -m unittest test_inference_runner.py -v`

Run: `cd backend && python -m py_compile applications/kml_roi/inference_runner.py applications/interface/mmseg_inference_caller.py applications/kml_roi/pipeline.py applications/kml_roi/service.py`

Expected: 全部退出码 0。

## Task 3: 通用 NVIDIA 设备解析与显式 CPU 降级

**Files:**
- Create: `backend/applications/inference/device.py`
- Test: `backend/test_inference_device.py`
- Modify: `backend/applications/configs/config.py`
- Modify: `backend/applications/interface/mmseg_segmentation.py`

- [ ] **Step 1: 写设备解析测试**

覆盖以下场景：

```python
def test_auto_uses_cuda_when_smoke_test_passes(): ...
def test_auto_falls_back_to_cpu_when_cuda_is_hidden(): ...
def test_auto_falls_back_when_mmcv_op_fails(): ...
def test_force_cpu_does_not_probe_cuda(): ...
def test_fallback_disabled_raises_device_error(): ...
```

断言结果结构：

```python
DeviceResolution(
    requested="auto",
    effective="cpu",
    fallback_reason="GPU_NOT_VISIBLE",
    warnings=("未检测到容器可见的 NVIDIA CUDA GPU，已回退 CPU",),
)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m unittest test_inference_device.py -v`

Expected: FAIL，提示 `DeviceResolver` 不存在。

- [ ] **Step 3: 实现 `DeviceResolver`**

模块定义：

```python
@dataclass(frozen=True)
class DeviceResolution:
    requested: str
    effective: str
    fallback_reason: Optional[str]
    warnings: Tuple[str, ...]
    gpu_name: Optional[str] = None
    compute_capability: Optional[str] = None


class DeviceResolutionError(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)
```

解析器依次检查 Torch CUDA、设备编号、张量运算和可注入的 MMCV/model smoke test。任何型号名称只用于展示，不参与选择逻辑。

- [ ] **Step 4: 增加配置项**

在 `BaseConfig` 增加：

```python
INFERENCE_ACCELERATOR = os.getenv("INFERENCE_ACCELERATOR", "auto")
INFERENCE_CPU_FALLBACK = _bool_env("INFERENCE_CPU_FALLBACK", True)
INFERENCE_GPU_DEVICE = int(os.getenv("INFERENCE_GPU_DEVICE") or 0)
INFERENCE_MAX_CONCURRENCY = int(os.getenv("INFERENCE_MAX_CONCURRENCY") or 1)
INFERENCE_JOB_TIMEOUT_SECONDS = int(os.getenv("INFERENCE_JOB_TIMEOUT_SECONDS") or 3600)
INFERENCE_KEEP_FAILED_WORKDIR = _bool_env("INFERENCE_KEEP_FAILED_WORKDIR", True)
```

- [ ] **Step 5: 运行设备测试**

Run: `cd backend && python -m unittest test_inference_device.py -v`

Expected: 所有模拟场景 PASS，测试不要求真实 GPU。

## Task 4: 推理任务模型、状态服务和 API

**Files:**
- Create: `backend/applications/models/inference_job.py`
- Create: `backend/applications/inference/jobs.py`
- Create: `backend/applications/api/inference.py`
- Modify: `backend/applications/models/__init__.py`
- Modify: `backend/applications/api/__init__.py`
- Test: `backend/test_inference_jobs.py`

- [ ] **Step 1: 写任务 API 测试**

```python
def test_create_job_returns_queued_job(self):
    self.login_as_admin()
    response = self.client.post("/api/inference/jobs", json=self.valid_payload())
    self.assertEqual(response.status_code, 201)
    data = self._json(response)["data"]
    self.assertEqual(data["status"], "queued")
    self.assertEqual(data["requested_device"], "auto")

def test_get_job_requires_login(self):
    response = self.client.get("/api/inference/jobs/missing")
    self.assertEqual(response.status_code, 401)
```

- [ ] **Step 2: 创建任务模型**

模型使用字符串 UUID 主键、Text JSON 字段和带索引的 `status/create_time`。状态服务只允许：

```text
queued -> running|cancelled
running -> succeeded|succeeded_with_fallback|partial_failed|failed|cancelled
```

- [ ] **Step 3: 实现创建、查询、取消和序列化**

创建任务时完成输入路径校验，并将规范化后的绝对路径写入 `request_payload`。取消接口只设置 `cancel_requested`，Worker 在瓦片边界停止。

- [ ] **Step 4: 注册 Blueprint 并运行测试**

Run: `cd backend && python -m unittest test_inference_jobs.py -v`

Expected: 鉴权、创建、查询、取消和非法状态转换全部 PASS。

## Task 5: 常驻模型 Worker

**Files:**
- Create: `backend/applications/inference/worker.py`
- Create: `backend/run_inference_worker.py`
- Test: `backend/test_inference_runner.py`
- Modify: `backend/applications/kml_roi/pipeline.py`
- Modify: `backend/applications/interface/mmseg_segmentation.py`

- [ ] **Step 1: 写模型只加载一次测试**

```python
def test_worker_reuses_loaded_model_across_jobs(self):
    loader = Mock(return_value=object())
    worker = InferenceWorker(model_loader=loader, inference_fn=Mock())
    worker.initialize()
    worker.run_job(self.job_a)
    worker.run_job(self.job_b)
    loader.assert_called_once()
```

- [ ] **Step 2: 写 OOM 回退测试**

第一次 CUDA 执行抛出 `torch.cuda.OutOfMemoryError`，最小批次重试再次抛错，CPU runner 成功。断言任务为 `succeeded_with_fallback`，原因是 `CUDA_OUT_OF_MEMORY`。

- [ ] **Step 3: 实现 Worker 生命周期**

`initialize()` 解析设备并加载模型；`run_forever()` 每次事务性领取一个任务；`run_job()` 创建独立工作目录、执行 pipeline、更新进度和最终状态。

- [ ] **Step 4: 让 pipeline 接受已加载模型 runner**

保留现有 CLI caller 作为兼容实现；Worker 注入直接调用 `inference_model(model, image)` 的 runner。两者返回相同结构化结果。

- [ ] **Step 5: 运行 Worker 测试**

Run: `cd backend && python -m unittest test_inference_runner.py test_inference_device.py test_inference_jobs.py -v`

Expected: 模型复用、取消、部分失败、OOM 回退全部 PASS。

## Task 6: 统一 Miner Node 和 Flask 入口

**Files:**
- Create: `miner/services/inferenceBackend.js`
- Test: `miner/test/inferenceBackend.test.js`
- Modify: `miner/server.js`
- Modify: `backend/applications/api/analysis.py`

- [ ] **Step 1: 写 Node 代理测试**

```javascript
test('createInferenceJob forwards payload and cookie to Flask', async () => {
  const calls = [];
  const client = createInferenceBackend({
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      return jsonResponse(201, { success: true, data: { id: 'job-1', status: 'queued' } });
    },
    backendBaseUrl: 'http://backend:5008',
  });
  const result = await client.createJob({ old_tif_path: '/data/a.tif' }, 'session=x');
  assert.equal(result.status, 201);
  assert.equal(calls[0].options.headers.cookie, 'session=x');
});
```

- [ ] **Step 2: 实现 Node 客户端并删除直接执行路径**

`miner/server.js` 的 `/api/inference/kml-roi` 代理到 Flask 规范接口。删除该路由中的 `execFile`、Python runner 解析和本地结果拼装，但保留其他用途仍在使用的 imports。

- [ ] **Step 3: 旧 Flask API 复用任务服务**

`/api/analysis/kml_roi_inference` 接受现有字段，创建任务并返回 `job_id/status/requested_device/effective_device`。同步兼容包装只在显式 `wait=true` 时等待结果，默认异步。

- [ ] **Step 4: 运行 Node 和 Flask API 测试**

Run: `cd miner && npm test`

Run: `cd backend && python -m unittest test_inference_jobs.py test_auth_api.py -v`

Expected: Node 全部测试和 Flask 鉴权/任务测试 PASS。

## Task 7: 双前端异步任务、进度和降级提示

**Files:**
- Modify: `frontend/src/api/upload.js`
- Modify: `frontend/src/utils/getUploadImg.js`
- Modify: `miner/src/composables/useMineData.js`
- Modify: `miner/src/components/InferenceModal.vue`
- Modify: `miner/src/components/MapDashboard.vue`
- Test: `miner/test/kmlInferenceArgs.test.js`

- [ ] **Step 1: 增加任务提交与轮询客户端**

GeoView 增加 `createInferenceJob`、`getInferenceJob`；Miner `useMineData` 增加 `pollInferenceJob(jobId, intervalMs=1000)`，遇到终态停止。

- [ ] **Step 2: 删除批量 `Promise.all`**

GeoView 对多个 TIF 逐个提交任务，按 job id 展示队列，不再共享一个全屏等待请求。默认请求设备由 `'cpu'` 改成 `'auto'`。

- [ ] **Step 3: 更新 Miner 弹窗**

设备选项只保留：

```html
<option value="auto">自动（优先 NVIDIA GPU）</option>
<option value="cpu">仅 CPU</option>
```

显示进度、实际设备、降级原因和逐瓦片失败数。

- [ ] **Step 4: 运行测试和构建**

Run: `cd miner && npm test && npm run build`

Run: `cd frontend && npm run build`

Expected: 测试 PASS，两个构建退出码 0，无模板或打包错误。

## Task 8: Docker GPU override、Worker 服务和运行时检查

**Files:**
- Create: `docker-compose.gpu.yml`
- Create: `docker/start-inference-worker.sh`
- Create: `docker/check-inference-runtime.py`
- Modify: `docker-compose.prod.yml`
- Modify: `backend/requirements-hf.txt`
- Modify: `image_bundle.env`

- [ ] **Step 1: 增加 CPU 安全 Worker 服务**

基础 Compose 中 `inference-worker` 不请求 GPU，挂载 backend、模型、上传、结果卷和任务运行目录，环境默认 `INFERENCE_ACCELERATOR=auto`、`INFERENCE_CPU_FALLBACK=true`。

- [ ] **Step 2: 增加 GPU override**

```yaml
services:
  inference-worker:
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
```

- [ ] **Step 3: 增加运行时检查**

检查脚本输出 JSON：Torch、Torch CUDA、MMCV、MMEngine、MMSeg 版本、CUDA 可用性、设备数、GPU 名称、compute capability、有效设备和降级原因。脚本退出码仅在 CPU 降级也不可用时非零。

- [ ] **Step 4: 固化依赖**

将最终通过模型 smoke test 的 Torch/CUDA/MMCV/MMEngine/MMSeg 组合写成精确版本和镜像摘要；删除运行时修改 site-packages 的 `_patch_mmdet_mmcv_guard`。

- [ ] **Step 5: 验证 Compose**

Run: `docker compose -f docker-compose.prod.yml config --quiet`

Run: `docker compose -f docker-compose.prod.yml -f docker-compose.gpu.yml config --quiet`

Expected: 两条命令退出码 0；基础配置无 GPU reservation，override 仅给 Worker 分配 GPU。

## Task 9: 错误语义、凭据和生产运行修复

**Files:**
- Modify: `backend/app.py`
- Modify: `backend/applications/common/utils/http.py`
- Modify: `docker-compose.prod.yml`
- Modify: `docker/start-backend.sh`
- Test: `backend/test_auth_api.py`

- [ ] **Step 1: 写 HTTP 状态测试**

未知 API 返回 404；未处理异常返回 500 且响应不包含 Python 异常栈；鉴权失败继续返回 401。

- [ ] **Step 2: 修正全局异常处理**

对 `HTTPException` 保留其状态；其他异常写日志并返回通用 500。`fail_api` 接受可选 HTTP status，但保持业务 `code` 字段兼容。

- [ ] **Step 3: 移除 Compose 明文密码和默认 MySQL 端口暴露**

`MYSQL_PASSWORD`、`MYSQL_ROOT_PASSWORD` 改成必填环境变量；生产默认不声明 `3307:3306`。

- [ ] **Step 4: 使用生产 WSGI 服务**

添加锁定版本 Gunicorn，`start-backend.sh` 使用单独 Web worker 配置启动 Flask；推理不在 Web worker 内执行。

- [ ] **Step 5: 运行后端安全回归**

Run: `cd backend && python -m unittest test_auth_api.py test_project_api.py test_inference_jobs.py -v`

Expected: 全部 PASS。

## Task 10: 文档、离线包与端到端验收

**Files:**
- Modify: `docs/offline_deployment_guide.md`
- Modify: `docs/system_guide.md`
- Create: `docs/inference_gpu_compatibility.md`
- Modify: `docs/test_report_template.md`
- Modify: `docs/superpowers/progress/2026-07-15-nvidia-inference-remediation-progress.md`

- [ ] **Step 1: 写 CPU 部署流程**

记录基础 Compose 启动、capabilities 检查、CPU 任务提交和结果验证。

- [ ] **Step 2: 写 NVIDIA GPU 部署流程**

记录宿主机驱动、NVIDIA Container Toolkit、GPU override、容器内 runtime check、实际设备和降级检查。

- [ ] **Step 3: 写离线要求和回滚**

离线包记录镜像 SHA256、依赖版本、Toolkit 包来源、CPU-only 回滚命令和故障排查错误码。

- [ ] **Step 4: 执行完整自动化验证**

Run: `cd miner && npm test && npm run build`

Run: `cd frontend && npm run build`

Run: `cd backend && python -m unittest discover -s . -p "test*.py"`

Run: `docker compose -f docker-compose.prod.yml config --quiet`

Run: `docker compose -f docker-compose.prod.yml -f docker-compose.gpu.yml config --quiet`

Expected: 全部退出码 0。

- [ ] **Step 5: 执行 CPU/GPU 黄金样例**

同一组固定 ROI 分别运行 CPU 和 GPU，记录设备、模型版本、耗时、峰值显存、类别图像素一致率、类别占比和变化矩阵差异。验收要求像素一致率不低于 99.5%。

- [ ] **Step 6: 更新进度台账和最终限制**

每完成一个 Task，在进度文件写入实际命令、结果、未验证项和回滚点。当前 Git 元数据缺失期间不得伪造 commit；Git 恢复后按 Task 粒度补做独立提交。
