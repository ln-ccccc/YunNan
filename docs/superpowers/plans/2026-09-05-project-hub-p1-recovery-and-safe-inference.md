# Project Hub P1 恢复与安全推理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` (recommended) or `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复自定义 FID 导入、失败底图恢复和受控影像启动推理三条 P1 主流程，并保证浏览器不再传输或读取服务器物理路径。

**Architecture:** 矿山预览只返回结构诊断，写入时才验证 FID；空间任务的终态不再被当作进行中，并由纯前端函数决定向导步骤。项目化推理继续使用既有 `/api/inference/jobs` 和 Worker，只把浏览器输入切换为项目内 `dataset_id`，后端在新的受控输入解析器中还原私有路径和项目空间范围；公共任务 DTO 统一脱敏。

**Tech Stack:** Python/Flask/SQLAlchemy/Rasterio、Python `unittest`、Node 原生测试、Express、Vue 3/Vite。

---

## 适用范围与前置条件

- 本计划对应 `docs/superpowers/specs/2026-09-05-project-hub-p1-recovery-design.md`，不改变模型、Worker 算法、数据表结构、离线目录和 GeoView 上传入口。
- 工作目录必须是隔离 worktree `D:\项目\YunNan\.worktrees\feat-project-hub-low-coupling-foundation`；不要在主工作区编码。
- 后端测试在项目固定的 Flask/GDAL 运行环境或 Docker 测试容器执行；Miner 测试在 `miner/` 下执行。
- 每个任务都先添加红灯测试，确认失败属于目标缺口后才进行最小实现；避免在一个提交中混入无关清理。

## 文件与职责

| 路径 | 动作 | 唯一职责 |
| --- | --- | --- |
| `backend/applications/project_hub/spatial_service.py` | 修改 | 非阻塞的矿山预览 FID 诊断 |
| `backend/applications/project_hub/spatial_state.py` | 修改 | 当前空间资源状态优先级 |
| `backend/test_project_spatial.py` | 修改 | FID 预览、导入和状态恢复回归 |
| `miner/src/projectWorkspace/projectWorkspaceViewModel.js` | 修改 | 可测试的空间向导步骤映射 |
| `miner/src/components/projectWorkspace/ProjectSpatialResources.vue` | 修改 | 终态任务恢复 UI，不计算领域状态 |
| `miner/test/projectWorkspaceViewModel.test.js` | 修改 | 向导步骤纯函数回归 |
| `backend/applications/project_hub/inference_inputs.py` | 新建 | 项目数据集 ID 到私有推理输入的唯一解析器 |
| `backend/applications/api/inference.py` | 修改 | 安全请求白名单、门禁、任务创建 |
| `backend/applications/inference/jobs.py` | 修改 | 私有载荷保存与公共任务 DTO 脱敏 |
| `backend/test_inference_jobs.py` | 修改 | 安全任务输入、跨项目隔离和脱敏回归 |
| `miner/server.js` | 修改 | `/api/inference/jobs` 同源透明转发 |
| `miner/test/inferenceBackend.test.js` | 修改 | canonical job 请求与 Cookie 转发 |
| `miner/src/components/InferenceModal.vue` | 修改 | 公开影像选择与安全请求表单 |
| `miner/src/components/MapDashboard.vue` | 修改 | 唯一弹窗协调与公开资产加载 |
| `miner/src/composables/useMineData.js` | 修改 | 使用 `dataset_id` 创建并轮询任务 |
| `miner/src/components/projectWorkspace/ProjectOverviewPanel.vue` | 修改 | 消费 capability 的启动入口 |
| `miner/src/components/ProjectWorkspace.vue` | 修改 | 将“开始地物分类”转为一次性地图打开请求 |
| `miner/src/App.vue` | 修改 | 项目地图与一次性打开推理弹窗的导航参数 |
| `docs/architecture/project-structure-and-low-coupling-contract-v1.md` | 修改 | 固定预览、终态恢复和安全任务输入契约 |
| `docs/development_guide.md` | 修改 | 更新项目化推理的离线输入和验收命令 |

### Task 1: 用测试定义非阻塞 FID 预览和空间恢复状态

**Files:**

- Modify: `backend/test_project_spatial.py`
- Modify: `miner/test/projectWorkspaceViewModel.test.js`
- Modify: `backend/applications/project_hub/spatial_service.py`
- Modify: `backend/applications/project_hub/spatial_state.py`
- Modify: `miner/src/projectWorkspace/projectWorkspaceViewModel.js`
- Modify: `miner/src/components/projectWorkspace/ProjectSpatialResources.vue`

- [ ] **Step 1: 写出 FID 预览和活动资源优先级的失败测试。**

  在 `TestProjectSpatialState` 中将“重复 FID 预览直接抛错”的断言替换为预览诊断断言，并加入只含 `mine_code` 的样例：

  ```python
  custom_preview = preview_mine_vector("custom.geojson", json.dumps({
      "type": "FeatureCollection",
      "features": [{
          "type": "Feature",
          "properties": {"mine_code": 301, "name": "矿山 A"},
          "geometry": {"type": "Polygon", "coordinates": [[[102, 25], [102.1, 25], [102.1, 25.1], [102, 25]]]},
      }],
  }))
  self.assertEqual(custom_preview["suggested_mapping"]["fid"], None)
  self.assertEqual(custom_preview["suggested_fid_validation"]["status"], "needs_selection")

  duplicate_preview = preview_mine_vector("duplicate.geojson", json.dumps(duplicate_payload))
  self.assertEqual(duplicate_preview["suggested_fid_validation"]["status"], "invalid")
  ```

  使用已有项目对象添加一个 `failed` 底图资源和一个 `active` 底图资源，断言：

  ```python
  summary = _serialize_summary(project)
  self.assertTrue(summary["map_ready"])
  self.assertEqual(summary["spatial_status"], "ready")
  ```

- [ ] **Step 2: 写出纯前端向导步骤的失败测试。**

  在 `miner/test/projectWorkspaceViewModel.test.js` 导入尚不存在的函数并固定状态映射：

  ```js
  import { resolveSpatialWizardStep } from '../src/projectWorkspace/projectWorkspaceViewModel.js';

  test('terminal spatial job returns to candidate selection while active jobs stay on progress', () => {
    assert.equal(resolveSpatialWizardStep({
      missing_resources: ['basemap'], jobs: [{ status: 'failed' }],
    }), 3);
    assert.equal(resolveSpatialWizardStep({
      missing_resources: ['basemap'], jobs: [{ status: 'cancelled' }],
    }), 3);
    assert.equal(resolveSpatialWizardStep({
      missing_resources: ['basemap'], jobs: [{ status: 'running' }],
    }), 4);
  });
  test('spatial wizard still requires mine import before basemap selection', () => {
    assert.equal(resolveSpatialWizardStep({ missing_resources: ['mine_vector', 'basemap'], jobs: [] }), 2);
  });
  ```

- [ ] **Step 3: 先运行红灯测试。**

  Run:

  ```powershell
  Set-Location D:\项目\YunNan\.worktrees\feat-project-hub-low-coupling-foundation\backend
  python -m unittest test_project_spatial.TestProjectSpatialState -v
  Set-Location ..\miner
  node --test test/projectWorkspaceViewModel.test.js
  ```

  Expected: 现有预览仍会抛出 `未识别到唯一 FID` 或 duplicate 错误，且 JS 找不到 `resolveSpatialWizardStep`；其余存量测试不应失败。

- [ ] **Step 4: 实现最小 FID 诊断与当前资源状态优先级。**

  在 `spatial_service.py` 增加诊断函数，保留 `_validate_fids()` 供 `import_mine_vector()` 调用：

  ```python
  def _suggested_fid_validation(features, fid_field):
      if not fid_field:
          return {"status": "needs_selection", "message": "未识别到唯一 FID 字段，请手动选择"}
      try:
          _validate_fids(features, fid_field)
      except ValueError as exc:
          return {"status": "invalid", "message": str(exc)}
      return {"status": "valid", "message": None}
  ```

  `preview_mine_vector()` 必须返回该字段，而不是调用 `_validate_fids()` 抛出。`import_mine_vector()` 不改其最终 FID 校验和 `FID_1` 规范化写入。

  在 `spatial_state.py` 用以下顺序替换状态判定：

  ```python
  failed_missing = any(
      item.status == "failed" and item.resource_type in missing
      for item in resources
  )
  if any(item.status in {"pending", "processing"} for item in resources):
      status = "processing"
  elif not missing:
      status = "ready"
  elif failed_missing:
      status = "failed"
  else:
      status = "unconfigured"
  ```

- [ ] **Step 5: 实现纯步骤映射和终态恢复展示。**

  在 `projectWorkspaceViewModel.js` 新增：

  ```js
  export function resolveSpatialWizardStep(spatial = {}) {
    const jobs = Array.isArray(spatial.jobs) ? spatial.jobs : [];
    if (jobs.some((job) => ['queued', 'running'].includes(job?.status))) return 4;
    const missing = Array.isArray(spatial.missing_resources) ? spatial.missing_resources : [];
    if (missing.includes('mine_vector')) return 2;
    if (missing.includes('basemap')) return 3;
    return 4;
  }
  ```

  `ProjectSpatialResources.vue` 只调用该函数。第 3 步在候选列表前展示最近一个 `failed` 或 `cancelled` 任务的错误/状态和原任务重试按钮；不要把该终态任务重新计为进行中，也不要删除历史任务。

- [ ] **Step 6: 运行绿灯测试并提交这一个模块。**

  Run:

  ```powershell
  Set-Location D:\项目\YunNan\.worktrees\feat-project-hub-low-coupling-foundation\backend
  python -m unittest test_project_spatial.TestProjectSpatialState -v
  Set-Location ..\miner
  node --test test/projectWorkspaceViewModel.test.js
  git diff --check
  ```

  Expected: 新增恢复/预览测试及既有目标测试全部通过。

  ```powershell
  git add backend/applications/project_hub/spatial_service.py backend/applications/project_hub/spatial_state.py backend/test_project_spatial.py miner/src/projectWorkspace/projectWorkspaceViewModel.js miner/src/components/projectWorkspace/ProjectSpatialResources.vue miner/test/projectWorkspaceViewModel.test.js
  git commit -m "fix(project-hub): 恢复自定义FID与失败底图流程"
  ```

### Task 2: 用测试冻结安全的项目影像任务输入和公开脱敏

**Files:**

- Create: `backend/applications/project_hub/inference_inputs.py`
- Modify: `backend/applications/api/inference.py`
- Modify: `backend/applications/inference/jobs.py`
- Modify: `backend/test_inference_jobs.py`

- [ ] **Step 1: 添加安全项目任务 API 的红灯用例。**

  在 `TestInferenceJobAPI` 建立 `PROJECT_STORAGE_ROOT` 临时目录，并增加一个辅助方法：创建 active 项目、active mine/basemap、当前项目绑定、`incoming/scene.tif` 和其 `ProjectDataset`。对 `resolve_interpretation_scope` 采用局部 `patch()`，让 API 测试专注输入边界：

  ```python
  with patch("applications.api.inference.resolve_interpretation_scope", return_value={
      "mode": "project", "project_id": project.id, "mine_resource_id": mine.id,
      "vector_path": str(vector_path), "matched_fids": [101], "warnings": [],
  }):
      response = self.client.post("/api/inference/jobs", json={
          "project_id": project.id, "dataset_id": dataset.id, "year": "2024", "device": "auto",
      })
  self.assertEqual(response.status_code, 201)
  self.assertEqual(response.get_json()["data"]["request"], {
      "project_id": project.id, "dataset_id": dataset.id, "year": "2024",
      "mine_fids": [101], "requested_device": "auto",
  })
  ```

  同组用例必须分别断言 `old_tif_path`、`new_tif_path`、`kml_path`、`output_root`、`storage_key` 和未知字段返回 422 且任务数不增加；跨项目 `dataset_id`、归档项目、缺 readiness、绝对/越界/不存在的已保存键均返回 4xx 且不创建任务。

  加入不依赖 Flask 的 `serialize_job()` 回归：

  ```python
  job = SimpleNamespace(
      id="job-1", project_id=1, status="failed", requested_device="auto",
      effective_device=None, fallback_reason=None, warnings_json="[]", progress_current=0,
      progress_total=0, cancel_requested=False, create_time=None, started_at=None, finished_at=None,
      request_payload_json=json.dumps({"dataset_id": 8, "old_tif_path": "/secret/scene.tif"}),
      result_json=json.dumps({"output_root": "/secret/output", "written_fid_list": [101]}),
      error_code="FAILED", error_message="/secret/scene.tif 读取失败",
  )
  public = serialize_job(job)
  self.assertNotIn("old_tif_path", json.dumps(public, ensure_ascii=False))
  self.assertNotIn("/secret", json.dumps(public, ensure_ascii=False))
  self.assertEqual(public["result"]["written_fid_list"], [101])
  ```

- [ ] **Step 2: 运行红灯后确认是新契约未实现。**

  Run:

  ```powershell
  Set-Location D:\项目\YunNan\.worktrees\feat-project-hub-low-coupling-foundation\backend
  python -m unittest test_inference_jobs.TestInferenceJobPayload test_inference_jobs.TestInferenceJobAPI -v
  ```

  Expected: 安全 body 因缺 `old_tif_path` 失败、路径字段仍被接受或公开 DTO 仍泄露路径；不能因为登录、数据库或 mock 设置失败。

- [ ] **Step 3: 新建唯一的服务端输入解析器。**

  在 `inference_inputs.py` 实现 `resolve_project_inference_input(project_id, dataset_id, mine_fids=None)`，返回供 API 内部使用的字典：

  ```python
  {
      "project_id": 12,
      "dataset_id": 34,
      "tif_path": Path(...),
      "mine_resource_id": 56,
      "kml_path": Path(...),
      "mine_fids": [101, 102],
      "project_root": Path(...),
      "output_root": Path(...),
  }
  ```

  实现必须先读取 `get_project_overview(project_id)["capabilities"]["can_start_inference"]`，拒绝归档或未就绪项目；再验证数据集项目归属、`dataset_kind == "imagery"`、合法相对 `incoming/*.tif(f)` 键和真实普通文件。调用 `resolve_interpretation_scope(project_id, tif_path)`，拒绝非 `project` 或空匹配；用户指定的 FID 必须是 `matched_fids` 的非空子集。任何路径只留在返回值，绝不构造 HTTP DTO。

- [ ] **Step 4: 将任务 API 改为白名单输入。**

  在 `api/inference.py` 固定允许集合，先拒绝字段再解析：

  ```python
  SAFE_JOB_FIELDS = frozenset({"project_id", "dataset_id", "year", "mine_fids", "device"})
  def _validate_safe_job_payload(payload):
      extra = sorted(set(payload) - SAFE_JOB_FIELDS)
      if extra:
          raise InferenceRequestValidationError(f"不支持字段: {', '.join(extra)}")
  ```

  `create_inference_job_api()` 调用输入解析器，并将其私有结果送入既有 `normalize_job_request()`：

  ```python
  normalized = normalize_job_request({
      "project_id": scope["project_id"], "dataset_id": scope["dataset_id"],
      "mine_resource_id": scope["mine_resource_id"], "mine_fids": scope["mine_fids"],
      "old_tif_path": str(scope["tif_path"]), "new_tif_path": str(scope["tif_path"]),
      "kml_path": str(scope["kml_path"]), "output_root": str(scope["output_root"]),
      "year": str(payload.get("year") or ""), "device": payload.get("device"),
  }, allowed_roots=[scope["project_root"], scope["tif_path"]], allowed_output_roots=[scope["project_root"] / "outputs"])
  ```

  校验失败返回 `422`；资源不存在、归属/就绪/FID 业务错误返回明确 `400`；二者均不能调用 `create_job()`。

- [ ] **Step 5: 实现公共任务 DTO 脱敏。**

  在 `jobs.py` 保留数据库内部 `request_payload_json`，新增私有递归清洗函数，匹配大小写不敏感的 `file_path`、`source_path`、`normalized_path`、`tile_path`、`manifest_path`、`output_dir`、`output_root`、`work_dir`、`kml_path`、`old_tif_path`、`new_tif_path`。对于剩余字符串，若含绝对路径痕迹则替换为通用错误文案。`serialize_job()` 只能回显：

  ```python
  {"project_id", "dataset_id", "mine_resource_id", "mine_fids", "year", "old_year", "new_year", "requested_device", "limit"}
  ```

  并对 `result` 和 `error.message` 使用同一清洗器。`normalize_job_request()` 将安全审计字段 `dataset_id` 原样持久化为整数。

- [ ] **Step 6: 运行绿灯测试并提交这一后端边界。**

  Run:

  ```powershell
  Set-Location D:\项目\YunNan\.worktrees\feat-project-hub-low-coupling-foundation\backend
  python -m unittest test_inference_jobs.py test_project_spatial.py -v
  git diff --check
  ```

  Expected: 安全请求创建成功、Worker 私有 request 仍有必要路径、任何 HTTP `serialize_job` 响应无路径，既有任务规范化测试继续通过。

  ```powershell
  git add backend/applications/project_hub/inference_inputs.py backend/applications/api/inference.py backend/applications/inference/jobs.py backend/test_inference_jobs.py
  git commit -m "fix(inference): 使用项目受控影像创建任务"
  ```

### Task 3: 将 Miner 入口改为同一安全推理弹窗

**Files:**

- Modify: `miner/test/inferenceBackend.test.js`
- Modify: `miner/server.js`
- Modify: `miner/src/components/InferenceModal.vue`
- Modify: `miner/src/components/MapDashboard.vue`
- Modify: `miner/src/composables/useMineData.js`
- Modify: `miner/src/components/projectWorkspace/ProjectOverviewPanel.vue`
- Modify: `miner/src/components/ProjectWorkspace.vue`
- Modify: `miner/src/App.vue`

- [ ] **Step 1: 先让 BFF client 测试表达安全请求。**

  将 `inferenceBackend.test.js` 的路径型样例改为：

  ```js
  const payload = { project_id: 7, dataset_id: 12, year: '2024', device: 'auto' };
  const result = await client.createJob(payload, 'session=x');
  assert.equal(result.status, 201);
  assert.equal(calls[0].url, 'http://backend:5008/api/inference/jobs');
  assert.equal(calls[0].options.headers.cookie, 'session=x');
  assert.deepEqual(JSON.parse(calls[0].options.body), payload);
  ```

  增加 `assert.doesNotMatch(JSON.stringify(payload), /tif_path|kml_path|output_root|storage_key/)`，保证该入口测试不再为路径型使用方式背书。

- [ ] **Step 2: 运行红灯或基线测试。**

  Run:

  ```powershell
  Set-Location D:\项目\YunNan\.worktrees\feat-project-hub-low-coupling-foundation\miner
  node --test test/inferenceBackend.test.js
  ```

  Expected: 在前端修改前该服务转发测试可以通过；这是 BFF 透明转发的基线。随后 Vite build 会在旧 modal 引用被删除前失败，证明 UI 仍依赖路径字段。

- [ ] **Step 3: 增加 canonical BFF 转发，保留迁移别名。**

  在 `server.js` 提取同一个处理函数：

  ```js
  const relayInferenceJobCreate = async (req, res) => {
    try {
      return relayBackendResponse(res, await inferenceBackend.createJob(req.body || {}, req.headers.cookie || ''));
    } catch (err) {
      return res.status(502).json({ success: false, code: 1, msg: err?.message || String(err) });
    }
  };
  app.post('/api/inference/jobs', relayInferenceJobCreate);
  app.post('/api/inference/kml-roi', relayInferenceJobCreate);
  ```

  BFF 不解析或补充字段；后端负责对路径型 body 返回 422。`/api/inference/kml-roi` 仅作为到 canonical API 的短期同源别名，不修改 GeoView `/api/analysis/kml_roi_inference`。

- [ ] **Step 4: 重写弹窗为公开资产选择。**

  `InferenceModal.vue` 的 props 固定为 `visible`、`running`、`error`、`result`、`imageryAssets`、`assetsLoading`；emits 固定为 `close`、`load-imagery`、`submit`。删除 KML 上传、`oldTifPath`、`newTifPath`、`kmlPath`、`oldYear`、`newYear`、`limit`。表单只保留：

  ```js
  const formData = reactive({ datasetId: '', year: '', device: 'auto' });
  emit('submit', {
    datasetId: Number(formData.datasetId),
    year: String(formData.year || '').trim(),
    device: formData.device,
  });
  ```

  弹窗打开时 emit `load-imagery`；列表值来自公开资产 `source_id`，显示 `name`、`temporal.year_start/year_end` 和 `format`，不显示 `source_type`、`storage_key` 或路径。没有候选时展示“请先通过项目工作台登记并准备影像”。

- [ ] **Step 5: 由地图协调公开资产与安全 job 请求。**

  `MapDashboard.vue` 创建 `createProjectWorkspaceApi()` 实例，维护 `imageryAssets`、`imageryAssetsLoading`、`imageryAssetsError`，并在 `loadInferenceImagery()` 内调用：

  ```js
  const response = await projectApi.loadAssets(props.projectId, { type: 'imagery', status: 'ready' });
  imageryAssets.value = response.items || [];
  ```

  `useMineData.js` 的任务调用改为：

  ```js
  const runProjectInference = async ({ datasetId, year = '', device = 'auto' } = {}) => {
    const res = await axios.post(apiUrl('/api/inference/jobs'), {
      project_id: resolveProjectId(), dataset_id: Number(datasetId), year, device,
    });
    // 保持现有轮询、terminal error 和 loadData 调用逻辑。
  };
  ```

  `MapDashboard` 不再接收或转发路径；任务成功后按原逻辑刷新数据并尝试定位第一个 `written_fid_list`。

- [ ] **Step 6: 为工作台入口传递一次性打开请求。**

  `ProjectOverviewPanel.vue` 仅当 `overview.capabilities.can_start_inference` 为真时显示按钮并 emit `start-inference`。`ProjectWorkspace.vue` 仅转发当前项目：

  ```js
  function startInference() {
    if (!currentProjectId.value || !overview.value?.capabilities?.can_start_inference) return;
    emit('open-map', currentProjectId.value, null, { openInference: true });
  }
  ```

  `App.vue` 用递增 `inferenceRequestId` 保存一次性请求，传给 `MapDashboard`；`MapDashboard` watch 该 ID 后显示同一个弹窗。用户在地图侧点击“开始地物分类”也只切换该同一 `showInferenceModal`，不得复制第二套表单。

- [ ] **Step 7: 运行 Miner 回归和构建。**

  Run:

  ```powershell
  Set-Location D:\项目\YunNan\.worktrees\feat-project-hub-low-coupling-foundation\miner
  node --test test/inferenceBackend.test.js test/projectWorkspaceApi.test.js test/projectWorkspaceViewModel.test.js test/projectRoutes.test.js
  npm run build
  ```

  Expected: Node 测试通过，Vite 不存在路径型 modal 字段、未使用导入或 Vue template 编译错误。

- [ ] **Step 8: 提交 Miner 的单一入口改动。**

  ```powershell
  git add miner/server.js miner/test/inferenceBackend.test.js miner/src/components/InferenceModal.vue miner/src/components/MapDashboard.vue miner/src/composables/useMineData.js miner/src/components/projectWorkspace/ProjectOverviewPanel.vue miner/src/components/ProjectWorkspace.vue miner/src/App.vue
  git commit -m "fix(miner): 通过受控影像启动项目推理"
  ```

### Task 4: 同步契约并执行完整回归与模拟验收

**Files:**

- Modify: `docs/architecture/project-structure-and-low-coupling-contract-v1.md`
- Modify: `docs/development_guide.md`
- Modify: `docs/superpowers/plans/2026-09-05-project-hub-p1-recovery-and-safe-inference.md`

- [ ] **Step 1: 更新架构契约的三项不可变规则。**

  在矿山预览段明确“预览可返回 `suggested_fid_validation`，只有导入是 FID 写入校验”；在任务状态段明确终态 `failed/cancelled` 不属于 in-flight、历史失败资源不覆盖当前 active 资源的 ready 状态；在 `can_start_inference` 附近增加：

  ```text
  项目化任务浏览器请求只允许 project_id、dataset_id、year、mine_fids、device。
  dataset_id 必须引用当前项目可用 imagery 资产；服务器解析 storage_key 和私有路径。
  任何物理路径、KML 路径、输出目录和 storage_key 均不得进入浏览器请求或任务公开 DTO。
  ```

- [ ] **Step 2: 更新开发指南的操作说明。**

  将路径型推理表单说明改为：离线部署人员先将 TIFF 放入共享 `incoming/`，项目用户通过 `storage_key` 登记，再在“开始地物分类”选择公开影像资产。写明后端与 Worker 必须共享相同的 `PROJECT_STORAGE_ROOT` 挂载，并列出本计划的后端、Miner 单测与构建命令。

- [ ] **Step 3: 完整自动回归。**

  Run:

  ```powershell
  Set-Location D:\项目\YunNan\.worktrees\feat-project-hub-low-coupling-foundation\backend
  python -m unittest test_project_spatial.py test_inference_jobs.py test_project_api.py test_project_read_models.py -v
  Set-Location ..\miner
  npm test
  npm run build
  Set-Location ..
  git diff --check
  git status --short
  ```

  Expected: 所有可用测试、Miner build 和差异检查通过。若宿主缺 Flask/GDAL，改用既有容器命令并在交付中列出未能本机验证的原因。

- [ ] **Step 4: 按真实验收场景模拟并记录结果。**

  在临时 `PROJECT_STORAGE_ROOT` 和 testing Flask/Miner 组合中执行：

  ```text
  登录 → 建项目 → 仅 mine_code 的 GeoJSON 预览 → 手选 mine_code 导入
  → 提交一份坏底图并形成 failed job → 返回候选页 → 选择有效 TIF → Worker 激活
  → 将 scene.tif 放入 incoming → storage_key 登记 → 工作台点击开始地物分类
  → 地图弹窗选择 imagery asset → POST /api/inference/jobs
  → 验证请求与轮询响应没有物理路径、任务私有载荷有 Worker 所需路径
  ```

  不把临时影像、数据库、Docker 容器或模型权重加入 Git。若完整模型 Worker 不可用，使用任务创建与 mock 发布结果证明接口闭环，并明确它不是模型精度验收。

- [ ] **Step 5: 标记计划、提交文档并请求代码审查。**

  完成后将本计划对应复选框勾选为完成，并提交：

  ```powershell
  git add docs/architecture/project-structure-and-low-coupling-contract-v1.md docs/development_guide.md docs/superpowers/plans/2026-09-05-project-hub-p1-recovery-and-safe-inference.md
  git commit -m "docs(project-hub): 更新安全推理与恢复契约"
  ```

  然后运行 `git status --short`，使用代码审查流程检查未提交差异、测试证据、路径泄露、接口破坏及文档一致性。

## 计划自检

- 规格覆盖：Task 1 实现 FID 与失败底图恢复；Task 2 实现项目资产到任务的服务端桥接及脱敏；Task 3 实现唯一前端/BFF 启动器；Task 4 同步契约、回归和真实流程验收。
- 占位符：本文件未发现未完成标记、空泛的“补充测试”或未定义接口。
- 类型一致：浏览器输入统一使用 `dataset_id`，内部解析结果使用 `tif_path/kml_path/output_root`，公开 DTO 统一使用 `requested_device`，任务状态保留现有原始词汇。
