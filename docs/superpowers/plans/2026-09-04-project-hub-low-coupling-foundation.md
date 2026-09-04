# Project Hub Low-Coupling Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` (recommended) or `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变固定模型、GIS 处理链或现有项目 CRUD 兼容行为的前提下，为 Project Hub 建立后端拥有的项目概览、资产 Read Model、安全导出/配置快照和可拆分的 Miner 工作台。

**Architecture:** 先以现有项目、空间资源、数据集、分类成果、导出和快照记录构造只读 `ProjectOverviewView` 与 `ProjectAssetView`，不先创建通用资产表。Flask 负责状态、blocker、capability 与资产适配；Miner BFF 透明转发；Vue 只维护当前项目上下文并将 DTO 分发给纯展示组件。物理路径始终留在服务端，项目导出和配置快照写入项目 sandbox。

**Tech Stack:** Python/Flask/SQLAlchemy/Marshmallow、Node 20/Express、Vue 3/Vite、Node 原生测试、Python `unittest`、GDAL 现有运行镜像。

---

## 范围与拆分

本计划只实施“Project Hub 低耦合基础”，即架构契约中的 P0–P5。它是全项目低耦合治理的第一个可交付子项目。

本计划不改动：模型训练、模型权重、推理算法、GeoView 重写、浏览器大影像上传/切片、通用 RBAC、实时多人编辑、完整灾备或部署拓扑。推理运行时与成果发布的跨域收口、离线数据导入协议、发布制品治理各自另写计划，不能并入本计划。

当前后端本机 Python 环境缺少 Flask/GDAL 依赖；Python 验证须在已固定的 Python 3.10/GDAL 环境或项目运行镜像中执行，不能把宿主机缺依赖误判为代码失败。

## 文件结构与职责

| 路径 | 动作 | 职责 |
| --- | --- | --- |
| `tests/fixtures/project-hub-v1/*.json` | 新建 | 后端与 BFF 共用的公开 DTO/错误响应黄金样例 |
| `backend/applications/project_hub/readiness.py` | 新建 | 固定五项 readiness、blocker、capability、next action 计算 |
| `backend/applications/project_hub/assets.py` | 新建 | 现有记录到 `ProjectAssetView` 的适配、筛选、物理路径隔离 |
| `backend/applications/project_hub/project_storage.py` | 新建 | 项目 export/snapshot sandbox、原子 JSON 写入、路径校验 |
| `backend/applications/project_hub/service.py` | 修改 | 聚合 overview/assets、受控数据登记、导出/快照与活动记录 |
| `backend/applications/schemas/project.py` | 修改 | 公共 overview/assets/export/snapshot/activity DTO Schema |
| `backend/applications/api/project.py` | 修改 | `/overview`、`/assets`、安全 mutation 的 HTTP 边界与 session actor |
| `backend/test_project_read_models.py` | 新建 | Read Model 的数据库隔离、fixture、鉴权与过滤回归 |
| `backend/test_project_api.py` | 修改 | 受控 `storage_key`、导出/配置快照恢复的 API 回归 |
| `miner/services/projectBackend.js` | 修改 | overview/assets 的专用后端 Client 方法 |
| `miner/routes/projects.js` | 修改 | 同源 overview/assets 透明转发，保持错误码与 Cookie |
| `miner/test/projectRoutes.test.js` | 修改 | BFF 不推导状态、不篡改过滤条件或错误码 |
| `miner/src/projectWorkspace/projectWorkspaceApi.js` | 新建 | 浏览器侧唯一 Project Hub HTTP Client，可注入 HTTP 实现测试 |
| `miner/src/projectWorkspace/projectWorkspaceViewModel.js` | 新建 | 项目 slice 状态、失效矩阵、action 中文/导航映射等纯函数 |
| `miner/src/components/projectWorkspace/*.vue` | 新建 | 概览、资产、活动、导出/配置快照、空间资源、表单等单职责视图 |
| `miner/src/components/ProjectWorkspace.vue` | 修改 | 当前项目上下文、一次装配、mutation 后局部刷新、空间轮询协调 |
| `miner/test/projectWorkspaceApi.test.js` | 新建 | 浏览器 API client 的 URL、错误和请求体测试 |
| `miner/test/projectWorkspaceViewModel.test.js` | 新建 | slice、action 映射、selection revision 与失效矩阵测试 |
| `miner/test/projectWorkspaceHelpers.test.js` | 修改 | 保留项目筛选、补充展示映射回归 |
| `docs/architecture/project-structure-and-low-coupling-contract-v1.md` | 修改 | 冻结字段语义、兼容策略和安全迁移说明 |
| `docs/development_guide.md` | 修改 | 增加项目工作台验证命令和迁移说明 |

## 固定语义（编码前不得自行改名）

```text
lifecycle_status: draft | active | completed | archived
readiness.status: blocked | partial | ready
asset.status: registered | processing | ready | failed | superseded
spatial/inference job.status: queued | running | succeeded | failed | cancelled
```

- `Project.status` 和旧 API 的 `status` 在本计划内保留；新增 DTO 同时返回 `lifecycle_status`。
- `counts.running_jobs` 只统计原始状态为 `running` 的任务；新增 `counts.queued_jobs` 单独统计排队任务。
- 固定检查顺序：`PROJECT_PROFILE`、`MINE_BOUNDARY`、`ACTIVE_BASEMAP`、`INFERENCE_INPUT`、`REVIEWABLE_RESULT`。
- 后端返回 `action_code` 与 `target`，Vue 负责当前页面中文文案/导航；后端不返回 Vue 路由字符串。
- 新 Read Model 和新 UI 不得返回或展示 `file_path`、`source_path`、`normalized_path`、`tile_path`、`manifest_path`、`output_dir`。
- `source_type`、`source_id` 仅作为不透明的追溯元数据保留在读取 DTO 中；Vue 不能据此分支、显示表名或反向拼接后端 URL，只能以公开 `id` 和语义化 `asset_type` 处理资产。

### Task 1: 冻结共享 fixture 与契约的边界语义

**Files:**

- Create: `tests/fixtures/project-hub-v1/overview-blocked.json`
- Create: `tests/fixtures/project-hub-v1/overview-partial.json`
- Create: `tests/fixtures/project-hub-v1/overview-ready.json`
- Create: `tests/fixtures/project-hub-v1/assets-mixed.json`
- Create: `tests/fixtures/project-hub-v1/assets-empty.json`
- Create: `tests/fixtures/project-hub-v1/assets-invalid-filter.json`
- Modify: `docs/architecture/project-structure-and-low-coupling-contract-v1.md`

- [ ] **Step 1: 在契约中补齐不会由实现者猜测的字段语义。**

  在“ProjectOverviewView 契约”中明确以下字段并保持此顺序：

  ```json
  {
    "counts": {
      "mines": 0,
      "assets": 0,
      "queued_jobs": 0,
      "running_jobs": 0,
      "failed_assets": 0
    },
    "capabilities": {
      "can_open_map": false,
      "can_configure_spatial": true,
      "can_start_inference": false,
      "can_review_result": false,
      "can_export": false
    }
  }
  ```

  同时写明：`INFERENCE_INPUT` 仅在存在已验证的相对 `storage_key`、格式为 `tif`/`tiff` 的影像记录时通过；旧绝对路径记录只显示为 `registered`，不能作为推理前置条件。

  同一节还必须冻结 capability 的判定，避免实现者在 Flask、BFF、Vue 各写一遍：

  ```text
  can_configure_spatial = lifecycle_status != archived
  can_open_map           = 已激活矿山边界 && 已激活底图（归档项目仍可只读打开）
  can_start_inference    = lifecycle_status != archived && 前四项检查均 passed
  can_review_result      = 存在 vector_status 为 ready 或 ready_empty 的分类成果
  can_export             = can_review_result
  ```

  `readiness` 本身不受 lifecycle 影响；同一资产组合在 `active` 与 `archived` 下必须得到相同的 readiness。`can_start_inference` 是“可启动新任务”，不要求 `REVIEWABLE_RESULT` 通过。

- [ ] **Step 2: 创建阻塞项目黄金样例。**

  写入 `overview-blocked.json`，顶层保持 API 的 `data` 包装，使用固定项目 ID `42`。核心内容必须为：

  ```json
  {
    "data": {
      "project_id": 42,
      "lifecycle_status": "draft",
      "readiness": {
        "status": "blocked",
        "passed": 1,
        "total": 5,
        "checks": [
          {"code": "PROJECT_PROFILE", "status": "passed", "reason_code": null},
          {"code": "MINE_BOUNDARY", "status": "blocked", "reason_code": "NO_MINE_BOUNDARY"},
          {"code": "ACTIVE_BASEMAP", "status": "blocked", "reason_code": "NO_ACTIVE_BASEMAP"},
          {"code": "INFERENCE_INPUT", "status": "blocked", "reason_code": "NO_INFERENCE_INPUT"},
          {"code": "REVIEWABLE_RESULT", "status": "blocked", "reason_code": "NO_REVIEWABLE_RESULT"}
        ]
      },
      "next_actions": [
        {"action_code": "IMPORT_MINE_BOUNDARY", "target": "spatial_resource"},
        {"action_code": "CONFIGURE_BASEMAP", "target": "spatial_resource"},
        {"action_code": "REGISTER_INFERENCE_INPUT", "target": "dataset"}
      ]
    }
  }
  ```

  在完整文件中补齐 `summary`、`capabilities`、`blockers`、`counts` 和空 `recent_activity`；不要加入时间戳或物理路径。

- [ ] **Step 3: 创建 partial/ready/assets 黄金样例。**

  使用以下固定数据，避免测试依赖当前时间或自动 ID：

  | Fixture | 生命周期 | 通过检查 | 关键资产 |
  | --- | --- | --- | --- |
  | `overview-partial.json` | `active` | `PROJECT_PROFILE`、`MINE_BOUNDARY`、`INFERENCE_INPUT` | `mine_boundary:101`、`imagery:201`；缺活动底图和可审阅成果 |
  | `overview-ready.json` | `active` | 五项全部通过 | `mine_boundary:101`、`basemap:102`、`imagery:201`、`inference_result:301`、`vector_revision:401` |
  | `assets-mixed.json` | 无 | 无 | 8 类 asset type 各一条，覆盖五种公开 asset status |
  | `assets-empty.json` | 无 | 无 | `{ "data": { "items": [], "count": 0 } }` |
  | `assets-invalid-filter.json` | 无 | 无 | HTTP 400 风格 `{ "success": false, "code": 1, "msg": "资产类型不合法" }` |

  `assets-mixed.json` 中空间状态映射必须固定为：`pending -> registered`、`processing -> processing`、`active -> ready`、`failed -> failed`、`retained -> superseded`。每个 asset 只允许出现公共字段，递归检查不得含路径键。

- [ ] **Step 4: 用 JSON 解析器验证 fixture。**

  Run:

  ```powershell
  Get-ChildItem tests/fixtures/project-hub-v1/*.json | ForEach-Object {
    Get-Content -Raw $_ | ConvertFrom-Json | Out-Null
  }
  ```

  Expected: 命令退出码为 `0`，六个 fixture 都能解析。

- [ ] **Step 5: 提交契约 fixture。**

  ```powershell
  git add docs/architecture/project-structure-and-low-coupling-contract-v1.md tests/fixtures/project-hub-v1
  git commit -m "test(project-hub): 固定概览与资产契约样例"
  ```

### Task 2: 以失败测试定义后端 Read Model

**Files:**

- Create: `backend/test_project_read_models.py`
- Modify: `backend/test_project_api.py`
- Modify: `backend/test_project_spatial.py`
- Modify: `backend/test_project_inference_results.py`

- [ ] **Step 1: 写入独立的 Read Model 测试基座。**

  在 `backend/test_project_read_models.py` 使用既有 testing app、SQLite 内存库和临时 `PROJECT_STORAGE_ROOT`；固定 ID/时间，避免删除动态字段后做宽松比较。

  ```python
  class TestProjectReadModels(unittest.TestCase):
      def setUp(self):
          self.temp_dir = tempfile.TemporaryDirectory()
          self.previous_storage_root = os.environ.get("PROJECT_STORAGE_ROOT")
          os.environ["PROJECT_STORAGE_ROOT"] = str(Path(self.temp_dir.name) / "project_storage")
          self.app = create_app("testing")
          self.app.config["PROPAGATE_EXCEPTIONS"] = True
          self.client = self.app.test_client()
          self.ctx = self.app.app_context()
          self.ctx.push()
          db.create_all()
          self.login_as_admin()

      def tearDown(self):
          db.session.remove()
          db.drop_all()
          self.ctx.pop()
          if self.previous_storage_root is None:
              os.environ.pop("PROJECT_STORAGE_ROOT", None)
          else:
              os.environ["PROJECT_STORAGE_ROOT"] = self.previous_storage_root
          self.temp_dir.cleanup()
  ```

  添加 `load_fixture(name)`，从仓库根的 `tests/fixtures/project-hub-v1/` 读取 JSON。

- [ ] **Step 2: 写入会失败的 endpoint 测试。**

  至少实现以下测试方法；三个 overview fixture 必须以完整 JSON 比较，不能删除动态字段：

  ```python
  def test_overview_empty_project_matches_blocked_fixture(self):
      self.seed_blocked_project(project_id=42)
      response = self.client.get("/api/projects/42/overview")
      self.assertEqual(response.status_code, 200)
      self.assertEqual(self.json_body(response), self.load_fixture("overview-blocked.json"))

  def test_overview_partial_project_matches_fixture(self):
      self.seed_partial_project(project_id=42)
      response = self.client.get("/api/projects/42/overview")
      self.assertEqual(response.status_code, 200)
      self.assertEqual(self.json_body(response), self.load_fixture("overview-partial.json"))

  def test_overview_ready_project_matches_fixture(self):
      self.seed_ready_project(project_id=42)
      response = self.client.get("/api/projects/42/overview")
      self.assertEqual(response.status_code, 200)
      self.assertEqual(self.json_body(response), self.load_fixture("overview-ready.json"))
  ```

  其余七个测试必须分别断言：同一资产组合在 `active` 与 `archived` 下 readiness 相同；另一项目的空间/推理任务不计入 `counts`；八类资产齐全且无路径键；五种空间原始状态映射正确；`type/status` 单独和组合筛选正确；空项目等于 `assets-empty.json`、非法筛选等于 `assets-invalid-filter.json`；未登录为 401、已登录的不存在项目为 404。

  `test_assets_maps_all_existing_sources_and_hides_physical_paths` 必须递归断言响应不存在 `file_path`、`source_path`、`normalized_path`、`tile_path`、`manifest_path` 和 `output_dir`。

- [ ] **Step 3: 运行测试，确认失败原因是接口未实现。**

  Run（固定 Python 3.10/GDAL 环境或 backend 运行镜像内执行）:

  ```powershell
  Set-Location D:\项目\YunNan\backend
  python -m unittest test_project_read_models.py -v
  ```

  Expected: `GET /api/projects/42/overview` 与 `/assets` 返回 404；不得因 fixture、登录或测试库初始化失败而失败。

- [ ] **Step 4: 保留既有项目回归用例并补齐新语义断言。**

  在 `test_project_api.py` 中保留 CRUD、矿山绑定、数据集、归档、导出和恢复测试；只新增 `lifecycle_status` 与旧 `status` 同时存在的断言。不得修改 `test_project_spatial.py` 对 `map_ready` 和 `spatial_status` 的原有断言；新 Read Model 是增量接口，不能取代空间状态。

- [ ] **Step 5: 提交红灯测试。**

  ```powershell
  git add backend/test_project_read_models.py backend/test_project_api.py backend/test_project_spatial.py backend/test_project_inference_results.py
  git commit -m "test(project-hub): 定义状态与资产读取契约"
  ```

### Task 3: 实现 readiness 与资产适配层

**Files:**

- Create: `backend/applications/project_hub/readiness.py`
- Create: `backend/applications/project_hub/assets.py`
- Modify: `backend/applications/project_hub/service.py`
- Modify: `backend/applications/schemas/project.py`

- [ ] **Step 1: 在 `readiness.py` 实现固定检查和动作排序。**

  只接受已由服务层取得的 project、assets 和任务统计，禁止在 Vue 或 BFF 中复制规则。

  ```python
  CHECK_ORDER = (
      "PROJECT_PROFILE",
      "MINE_BOUNDARY",
      "ACTIVE_BASEMAP",
      "INFERENCE_INPUT",
      "REVIEWABLE_RESULT",
  )

  def build_readiness(project, assets):
      checks = [
          _project_profile_check(project),
          _asset_check(assets, "mine_boundary", "MINE_BOUNDARY", "NO_MINE_BOUNDARY"),
          _asset_check(assets, "basemap", "ACTIVE_BASEMAP", "NO_ACTIVE_BASEMAP"),
          _asset_check(assets, "imagery", "INFERENCE_INPUT", "NO_INFERENCE_INPUT"),
          _reviewable_result_check(assets),
      ]
      passed = sum(item["status"] == "passed" for item in checks)
      status = "ready" if passed == len(checks) else "blocked" if passed <= 1 else "partial"
      blockers = [{"code": item["reason_code"], "severity": "warning"}
                  for item in checks if item["status"] == "blocked"]
      return {"status": status, "passed": passed, "total": len(checks), "checks": checks}, blockers
  ```

  `build_next_actions` 必须按检查顺序返回 `IMPORT_MINE_BOUNDARY`、`CONFIGURE_BASEMAP`、`REGISTER_INFERENCE_INPUT`、`REVIEW_RESULT`；已有条件不再重复返回。

- [ ] **Step 2: 在 `assets.py` 实现无路径泄露的 `ProjectAssetView`。**

  用函数而不是通用数据库表适配现有来源。公共资产 ID 使用稳定复合 ID，例如 `spatial:17`、`dataset:21`、`classification-result:31`、`revision:31:2`、`export:41`、`snapshot:51`。

  ```python
  VALID_ASSET_TYPES = frozenset({
      "mine_boundary", "basemap", "imagery", "inference_result",
      "vector_revision", "report", "export", "backup_snapshot",
  })
  VALID_ASSET_STATUSES = frozenset({
      "registered", "processing", "ready", "failed", "superseded",
  })

  class ProjectAssetFilterError(ValueError):
      """Raised when a public ProjectAssetView filter is unsupported."""

  SPATIAL_STATUS_MAP = {
      "pending": "registered",
      "processing": "processing",
      "active": "ready",
      "failed": "failed",
      "retained": "superseded",
  }

  def list_project_assets(project, asset_type=None, status=None):
      if asset_type and asset_type not in VALID_ASSET_TYPES:
          raise ProjectAssetFilterError("资产类型不合法")
      if status and status not in VALID_ASSET_STATUSES:
          raise ProjectAssetFilterError("资产状态不合法")
      items = []
      items.extend(_spatial_assets(project.spatial_resources))
      items.extend(_dataset_assets(project.datasets))
      items.extend(_classification_assets(project.id))
      items.extend(_export_assets(project.exports))
      items.extend(_snapshot_assets(project.backups))
      items = _deduplicate_classification_datasets(items)
      return _filter_and_sort(items, asset_type=asset_type, status=status)
  ```

  `_dataset_assets` 必须跳过已经带有 `classification_result_id` 的 `inference_result` 数据集，避免和 `ClassificationResult` 重复。`_classification_assets` 将 `vector_status=ready|ready_empty` 映射为公开 `ready`，`vector_failed` 映射为 `failed`，其余未完成状态映射为 `processing`；`_reviewable_result_check` 只能接受前两者。任何 `source_path` 只能用于服务器内部构建 provenance，不能出现在返回字典中。

- [ ] **Step 3: 在 Schema 中只声明公共 DTO。**

  在 `schemas/project.py` 新增 `ProjectAssetViewSchema` 和 `ProjectOverviewViewSchema`。`ProjectAssetViewSchema` 只允许以下字段：

  ```text
  id, source_type, source_id, asset_type, name, format, status, version,
  created_at, updated_at, spatial, temporal, provenance, error, capabilities
  ```

  `source_type` / `source_id` 是服务器生成的不透明追溯字段，不可让 Vue 以其底层表名写业务判断或 URL。`ProjectOverviewViewSchema` 必须含 `project_id`、`lifecycle_status`、`summary`、`readiness`、`capabilities`、`blockers`、`next_actions`、`counts`、`recent_activity`。保留现有 `ProjectSummarySchema`，不得把 overview DTO 塞进旧 summary。

- [ ] **Step 4: 在 `service.py` 增加聚合读取入口。**

  使用 `_get_project_or_404`，不复制查询逻辑：

  ```python
  def get_project_overview(project_id):
      project = _get_project_or_404(project_id)
      assets = list_project_assets(project)
      readiness, blockers = build_readiness(project, assets)
      return ProjectOverviewViewSchema().dump({
          "project_id": project.id,
          "lifecycle_status": project.status,
          "summary": _serialize_summary(project),
          "readiness": readiness,
          "capabilities": build_capabilities(project, assets, readiness),
          "blockers": blockers,
          "next_actions": build_next_actions(readiness),
          "counts": build_project_counts(project, assets),
          "recent_activity": serialize_recent_activity(project.activities[:20]),
      })

  def get_project_assets(project_id, filters=None):
      project = _get_project_or_404(project_id)
      items = list_project_assets(project, **(filters or {}))
      return {"items": items, "count": len(items)}
  ```

  `build_project_counts` 对 `ProjectSpatialJob` 和 `InferenceJob` 均按 `project_id` 过滤，`queued_jobs` 与 `running_jobs` 分开。

- [ ] **Step 5: 运行后端 Read Model 测试并修复到全绿。**

  Run:

  ```powershell
  Set-Location D:\项目\YunNan\backend
  python -m unittest test_project_read_models.py -v
  ```

  Expected: 10 个 Read Model 用例通过，fixture 比较不删除字段、不忽略数组顺序。

- [ ] **Step 6: 提交后端领域实现。**

  ```powershell
  git add backend/applications/project_hub/readiness.py backend/applications/project_hub/assets.py backend/applications/project_hub/service.py backend/applications/schemas/project.py backend/test_project_read_models.py
  git commit -m "feat(project-hub): 增加项目概览与资产读取模型"
  ```

### Task 4: 发布 Flask/BFF 读取接口，保持现有接口兼容

**Files:**

- Modify: `backend/applications/api/project.py`
- Modify: `miner/services/projectBackend.js`
- Modify: `miner/routes/projects.js`
- Modify: `miner/test/projectRoutes.test.js`

- [ ] **Step 1: 为 `/overview` 和 `/assets` 先增加 API 测试。**

  在 `backend/test_project_read_models.py` 中对下列请求断言 envelope、HTTP 状态与 fixture：

  ```text
  GET /api/projects/42/overview
  GET /api/projects/42/assets
  GET /api/projects/42/assets?type=imagery
  GET /api/projects/42/assets?type=imagery&status=failed
  ```

  未登录必须 `401`；不存在项目必须 `404`；未知 `type` 或 `status` 必须 `400`。错误不能以 HTTP 200 的空列表伪装。

- [ ] **Step 2: 运行 endpoint 测试确认路由仍不存在。**

  Run:

  ```powershell
  Set-Location D:\项目\YunNan\backend
  python -m unittest test_project_read_models.TestProjectReadModels.test_read_model_endpoints_require_login_and_return_404_for_missing_project -v
  ```

  Expected: 在路由实现前失败于 404/缺少 handler。

- [ ] **Step 3: 在 Flask 项目路由中添加显式读取端点。**

  在 `project_detail_api` 前增加，并从 `assets.py` 导入 `ProjectAssetFilterError`：

  ```python
  @project_api.get("/<int:project_id>/overview")
  @login_required
  def project_overview_api(project_id):
      try:
          return success_api(data=get_project_overview(project_id))
      except ValueError as exc:
          return fail_api(str(exc), status=404)

  @project_api.get("/<int:project_id>/assets")
  @login_required
  def project_assets_api(project_id):
      filters = {
          "asset_type": request.args.get("type", type=str),
          "status": request.args.get("status", type=str),
      }
      try:
          return success_api(data=get_project_assets(project_id, filters))
      except ProjectAssetFilterError as exc:
          return fail_api(str(exc), status=400)
      except ValueError as exc:
          return fail_api(str(exc), status=404)
  ```

  先在 import 列表加入对应 service 函数。旧 `GET /{id}`、`/timeline`、`/spatial` 不删除、不改响应形状。

- [ ] **Step 4: 为 BFF 加专用 client 和透明路由。**

  在 `miner/services/projectBackend.js` 添加：

  ```js
  getProjectOverview(projectId, cookie) {
    return requestJson('GET', `/api/projects/${projectId}/overview`, { cookie });
  },
  listProjectAssets(projectId, query, cookie) {
    return requestJson('GET', `/api/projects/${projectId}/assets`, { query, cookie });
  },
  ```

  在 `miner/routes/projects.js` 的 `/:projectId` detail 路由之前添加：

  ```js
  router.get('/:projectId/overview', async (req, res) => {
    try {
      relayJson(res, await projectApi.getProjectOverview(req.params.projectId, requestCookie(req)));
    } catch (error) {
      res.status(502).json({ success: false, code: 1, msg: error?.message || String(error) });
    }
  });

  router.get('/:projectId/assets', async (req, res) => {
    try {
      relayJson(res, await projectApi.listProjectAssets(req.params.projectId, req.query || {}, requestCookie(req)));
    } catch (error) {
      res.status(502).json({ success: false, code: 1, msg: error?.message || String(error) });
    }
  });
  ```

- [ ] **Step 5: 添加 BFF 透明代理测试。**

  在 `miner/test/projectRoutes.test.js` 增加：

  ```js
  test('createProjectRoutes proxies overview without deriving readiness', async () => { /* fixture upstream */ });
  test('createProjectRoutes proxies asset filters unchanged', async () => { /* type/status/cookie */ });
  test('createProjectRoutes preserves read-model error status', async () => { /* 400 and 404 */ });
  ```

  每个 fake `projectApi` 必须断言 BFF 未排序 `checks`、`blockers`、`next_actions`，并把后端的 400/404 原样返回；仅网络异常才转换为 502。

- [ ] **Step 6: 执行接口与 BFF 回归。**

  Run:

  ```powershell
  Set-Location D:\项目\YunNan\backend
  python -m unittest test_project_read_models.py test_project_api.py -v

  Set-Location ..\miner
  node --check services/projectBackend.js
  node --check routes/projects.js
  npm test -- test/projectRoutes.test.js
  ```

  Expected: 旧项目 CRUD、空间接口与 BFF 的三条既有路由测试继续通过。

- [ ] **Step 7: 提交 HTTP 契约实现。**

  ```powershell
  git add backend/applications/api/project.py miner/services/projectBackend.js miner/routes/projects.js miner/test/projectRoutes.test.js backend/test_project_read_models.py
  git commit -m "feat(project-hub): 发布概览和资产读取接口"
  ```

### Task 5: 收紧数据登记、导出与配置快照的存储边界

**Files:**

- Create: `backend/applications/project_hub/project_storage.py`
- Modify: `backend/applications/project_hub/service.py`
- Modify: `backend/applications/api/project.py`
- Modify: `backend/applications/schemas/project.py`
- Modify: `backend/test_project_api.py`
- Modify: `miner/routes/projects.js`

- [ ] **Step 1: 为安全路径和 manifest 写失败测试。**

  在 `test_project_api.py` 增加以下测试：

  ```python
  def test_dataset_registration_rejects_absolute_or_escaping_storage_key(self):
      project_id = self.create_project("安全数据集")
      for storage_key in ("D:/outside.tif", "../outside.tif"):
          response = self.client.post(
              f"/api/projects/{project_id}/datasets",
              json={"display_name": "非法影像", "dataset_kind": "imagery", "storage_key": storage_key},
          )
          self.assertEqual(response.status_code, 422)

  def test_export_rejects_output_dir_and_writes_project_sandbox_manifest(self):
      project_id = self.create_project("安全导出")
      rejected = self.client.post(f"/api/projects/{project_id}/exports", json={"format": "csv", "output_dir": "D:/tmp"})
      self.assertEqual(rejected.status_code, 422)
      created = self.client.post(f"/api/projects/{project_id}/exports", json={"format": "csv"})
      self.assertEqual(created.status_code, 200)
      export_id = self.json_body(created)["data"]["id"]
      expected_manifest = Path(os.environ["PROJECT_STORAGE_ROOT"]) / "projects" / str(project_id) / "exports" / str(export_id) / "manifest.json"
      self.assertTrue(expected_manifest.is_file())
  ```

  另外两个测试必须断言：创建配置快照后记录的公共名称为“项目配置快照”且 manifest 位于 `PROJECT_STORAGE_ROOT/projects/{project_id}/snapshots/{snapshot_id}/manifest.json`；恢复时内部 manifest 可恢复项目元数据，而公开 API 响应中没有 `manifest_path`。

- [ ] **Step 2: 运行测试确认当前实现仍允许任意输出目录。**

  Run:

  ```powershell
  Set-Location D:\项目\YunNan\backend
  python -m unittest test_project_api.TestProjectAPI.test_project_export_and_backup_restore -v
  ```

  Expected: 新断言在当前 `output_dir` 行为处失败；不得删除原有恢复覆盖测试。

- [ ] **Step 3: 实现唯一的项目 sandbox 路径服务。**

  新建 `project_storage.py`，复用 `spatial_storage.get_storage_root()`，不复制环境变量读取逻辑：

  ```python
  from pathlib import Path
  from applications.project_hub.spatial_storage import get_storage_root, resolve_storage_path

  def project_root(project_id):
      return resolve_storage_path(get_storage_root(), Path("projects") / str(int(project_id)))

  def export_root(project_id, export_id):
      path = project_root(project_id) / "exports" / str(int(export_id))
      path.mkdir(parents=True, exist_ok=True)
      return path

  def snapshot_root(project_id, snapshot_id):
      path = project_root(project_id) / "snapshots" / str(int(snapshot_id))
      path.mkdir(parents=True, exist_ok=True)
      return path
  ```

  `write_json_atomic(path, payload)` 必须同目录临时文件写入后 `os.replace`；禁止 `Path` 拼接绕过 `resolve_storage_path`。

- [ ] **Step 4: 改造 dataset、export 与 snapshot 服务的输入和输出。**

  - `create_dataset` 的浏览器输入改为 `storage_key`，仅接受 `incoming/` 下的相对 TIFF/TIFF 文件；存量列 `ProjectDataset.file_path` 暂存相对 key，避免立即迁移数据库。
  - API 若收到 `file_path` 或 `output_dir`，返回 `422` 和明确迁移提示，不能静默忽略。
  - `create_export` 先写 `ProjectExportRecord(status="pending")` 并 flush ID，再写入 `exports/{id}/artifact.<suffix>` 与 `manifest.json`，成功后更新 `completed`；失败时记录 `failed`。
  - `create_backup` 在 UI/API 中改名为“项目配置快照”，先写 `ProjectBackupRecord` 再创建 `snapshots/{id}/manifest.json`。
  - `_project_manifest` 改用内部 serializer 保存恢复所需存储引用；公共 `ProjectExportRecordSchema` / `ProjectBackupRecordSchema` 改为 `artifact_name` / `snapshot_name`、状态、时间、`restorable`，不序列化物理绝对路径。

- [ ] **Step 5: 更新 BFF 导出转发，不再接受浏览器目录。**

  在 `miner/routes/projects.js` 的 exports 路由中先拒绝 `output_dir`，再构造转发 payload；不得先删除字段后继续执行，以免客户端误以为目录选择被支持。保留由 BFF 服务端生成的 GeoJSON feature 逻辑：

  ```js
  if (Object.hasOwn(req.body || {}, 'output_dir')) {
    return res.status(422).json({ success: false, code: 1, msg: '不支持指定服务端输出目录，请移除 output_dir' });
  }
  const payload = { ...(req.body || {}) };
  ```

- [ ] **Step 6: 运行存储安全与恢复回归。**

  Run:

  ```powershell
  Set-Location D:\项目\YunNan\backend
  python -m unittest test_project_api.py test_project_spatial.py -v

  Set-Location ..\miner
  npm test -- test/projectRoutes.test.js
  ```

  Expected: 任意绝对/越界路径被拒绝，历史项目 CRUD 与空间路径防越界测试仍通过。

- [ ] **Step 7: 提交存储收口。**

  ```powershell
  git add backend/applications/project_hub/project_storage.py backend/applications/project_hub/service.py backend/applications/api/project.py backend/applications/schemas/project.py backend/test_project_api.py miner/routes/projects.js miner/test/projectRoutes.test.js
  git commit -m "fix(project-hub): 收紧项目导出与配置快照路径"
  ```

### Task 6: 让活动记录成为机器可读审计事件

**Files:**

- Modify: `backend/applications/project_hub/service.py`
- Modify: `backend/applications/project_hub/spatial_service.py`
- Modify: `backend/applications/api/project.py`
- Modify: `backend/test_project_read_models.py`
- Modify: `backend/test_project_api.py`
- Modify: `miner/src/projectWorkspace/projectWorkspaceViewModel.js`
- Modify: `miner/test/projectWorkspaceViewModel.test.js`

- [ ] **Step 1: 为活动 DTO 写入失败测试。**

  测试至少覆盖：创建项目、更新项目、登记数据、空间任务入队、导出、配置快照和恢复配置快照。每条公开活动必须带：

  ```json
  {
    "event_type": "export_created",
    "action_code": "EXPORT_CREATED",
    "actor": "admin",
    "target": {"type": "export", "id": "41"},
    "result": "success",
    "payload": {},
    "created_at": "2026-09-04T10:00:00",
    "timestamp": "2026-09-04T10:00:00"
  }
  ```

  历史 `event_type` 和既有 `timestamp` 仍应原样作为兼容字段返回；断言 `event_type="export_created"` 被规范化为 `action_code="EXPORT_CREATED"`，而 `timestamp` 与新增 `created_at` 相同，不能强行让事件名两者字面相等。

- [ ] **Step 2: 运行测试确认当前只有技术 event_type。**

  Run:

  ```powershell
  Set-Location D:\项目\YunNan\backend
  python -m unittest test_project_read_models.py test_project_api.py -v
  ```

  Expected: 在 `action_code`、`target`、真实 session actor 缺失处失败。

- [ ] **Step 3: 保持旧表结构与 event_type 兼容，扩展公共 serializer。**

  `ProjectActivityLog` 不迁移表。既有调用方继续传入当前小写 `event_type`（例如 `project_created`、`spatial_job_queued`、`export_created`）；不要把已存在事件值改成大写。新增规范化映射，并改造 `_append_activity`：

  ```python
  ACTION_CODE_BY_EVENT_TYPE = {
      "project_created": "PROJECT_CREATED",
      "project_updated": "PROJECT_UPDATED",
      "mine_binding_replaced": "MINE_BINDING_REPLACED",
      "dataset_created": "DATASET_REGISTERED",
      "project_archived": "PROJECT_ARCHIVED",
      "project_restored": "PROJECT_RESTORED",
      "spatial_resource_removed": "SPATIAL_RESOURCE_REMOVED",
      "spatial_resource_activated": "SPATIAL_RESOURCE_ACTIVATED",
      "spatial_job_queued": "SPATIAL_JOB_QUEUED",
      "spatial_job_retried": "SPATIAL_JOB_RETRIED",
      "spatial_job_cancel_requested": "SPATIAL_JOB_CANCEL_REQUESTED",
      "export_created": "EXPORT_CREATED",
      "backup_created": "SNAPSHOT_CREATED",
      "backup_restored": "SNAPSHOT_RESTORED",
  }

  def _append_activity(project_id, event_type, payload=None, actor="system", target=None, result="success"):
      event_payload = dict(payload or {})
      event_payload.setdefault("target", target or {})
      event_payload.setdefault("result", result)
      row = ProjectActivityLog(
          project_id=project_id,
          event_type=event_type,
          actor=actor or "system",
          payload_json=_json_dump(event_payload),
      )
      db.session.add(row)
      return row
  ```

  `get_project_timeline` / `serialize_recent_activity` 返回 `event_type` 原值、`action_code=ACTION_CODE_BY_EVENT_TYPE.get(event_type, event_type.upper())`、`target=payload.target`、`result=payload.result`、`created_at`，并保留 `timestamp=created_at`。旧事件没有字段时返回 `{}` 和 `success`，不能抛异常。新写入的 `target` 必须有语义类型和公开 ID；历史记录缺少时只返回空对象，不能伪造目标。`spatial_service.py` 中现有 `_activity` 也必须采用相同 payload 约定并接受可选 `actor`，避免空间事件绕过审计格式。

- [ ] **Step 4: 从 HTTP 会话传入操作者，而不引入成员系统。**

  在 `api/project.py` 增加：

  ```python
  def _request_actor():
      return str(session.get("admin_username") or "system")
  ```

  为 `create_project`、`update_project`、`create_dataset`、归档、恢复、导出、快照、恢复快照增加可选 `actor` 参数并从路由传入。空间服务的 import/register/retry/cancel 同样增加可选 `actor`，其 `_activity` 调用透传该值；默认保留 `system` 给 worker 调用。

- [ ] **Step 5: 在 Miner View Model 做纯展示映射。**

  只映射文案，不推断业务规则：

  ```js
  export const ACTIVITY_LABELS = {
    PROJECT_CREATED: '创建项目',
    PROJECT_UPDATED: '更新项目',
    SPATIAL_JOB_QUEUED: '提交空间处理任务',
    EXPORT_CREATED: '生成导出成果',
    SNAPSHOT_CREATED: '生成项目配置快照',
    SNAPSHOT_RESTORED: '恢复项目配置快照',
  };

  export function formatActivityAction(actionCode) {
    return ACTIVITY_LABELS[actionCode] || actionCode || '未知操作';
  }
  ```

- [ ] **Step 6: 运行活动回归并提交。**

  ```powershell
  Set-Location D:\项目\YunNan\backend
  python -m unittest test_project_read_models.py test_project_api.py test_project_spatial.py -v

  Set-Location ..\miner
  node --test test/projectWorkspaceViewModel.test.js

  git add backend/applications/project_hub/service.py backend/applications/project_hub/spatial_service.py backend/applications/api/project.py backend/test_project_read_models.py backend/test_project_api.py miner/src/projectWorkspace/projectWorkspaceViewModel.js miner/test/projectWorkspaceViewModel.test.js
  git commit -m "feat(project-hub): 结构化项目活动审计"
  ```

### Task 7: 建立 Miner 工作台 API Client 与父协调器数据流

**Files:**

- Create: `miner/src/projectWorkspace/projectWorkspaceApi.js`
- Create: `miner/src/projectWorkspace/projectWorkspaceViewModel.js`
- Create: `miner/test/projectWorkspaceApi.test.js`
- Create: `miner/test/projectWorkspaceViewModel.test.js`
- Modify: `miner/src/projectWorkspace/projectWorkspaceHelpers.js`
- Modify: `miner/src/components/ProjectWorkspace.vue`

- [ ] **Step 1: 为浏览器 API client 与选择竞争写失败测试。**

  `projectWorkspaceApi.test.js` 必须使用可注入 `http`，不启动 Vue。测试下面调用：

  ```js
  api.loadOverview(42);
  api.loadAssets(42, { type: 'imagery', status: 'failed' });
  api.createExport(42, { format: 'geojson' });
  api.createSnapshot(42);
  ```

  断言 URL 为 `/api/projects/42/overview`、`/assets?type=imagery&status=failed`，请求体没有 `output_dir` 或 `file_path`。`projectWorkspaceViewModel.test.js` 断言较早项目选择的响应不能覆盖最新项目选择。

- [ ] **Step 2: 实现唯一的工作台 HTTP client。**

  复用现有 Vite base URL，所有组件通过本 client 请求：

  ```js
  import axios from 'axios';

  export function createProjectWorkspaceApi({ http = axios, baseUrl = '' } = {}) {
    const url = (path) => `${baseUrl}${path}`;
    const data = async (request) => {
      const response = await request;
      if (response?.data?.success === false) throw new Error(response.data.msg || '项目请求失败');
      return response?.data?.data ?? response?.data;
    };
    return {
      loadProjects: () => data(http.get(url('/api/projects'))),
      loadDetail: (projectId) => data(http.get(url(`/api/projects/${projectId}`))),
      loadOverview: (projectId) => data(http.get(url(`/api/projects/${projectId}/overview`))),
      loadAssets: (projectId, params = {}) => data(http.get(url(`/api/projects/${projectId}/assets`), { params })),
      loadActivity: (projectId) => data(http.get(url(`/api/projects/${projectId}/timeline`))),
      loadExports: (projectId) => data(http.get(url(`/api/projects/${projectId}/exports`))),
      loadSnapshots: (projectId) => data(http.get(url(`/api/projects/${projectId}/backups`))),
      createExport: (projectId, payload) => data(http.post(url(`/api/projects/${projectId}/exports`), payload)),
      createSnapshot: (projectId) => data(http.post(url(`/api/projects/${projectId}/backups`), {})),
      registerDataset: (projectId, payload) => data(http.post(url(`/api/projects/${projectId}/datasets`), payload)),
    };
  }
  ```

  在同一 return 对象中补齐已有 mutation：`createProject`、`updateProject`、`replaceProjectMines`、`previewMineVector`、`importMineVector`、`listBasemapCandidates`、`registerBasemap`、`retrySpatialJob`、`cancelSpatialJob`、`archiveProject`、`restoreProject`、`restoreSnapshot`。每个方法只封装 HTTP method、公开 URL 和 request body；不得添加 readiness/资产推断。`registerDataset` 的 payload 仅允许 `display_name`、`dataset_kind`、相对 `storage_key` 和约定元数据，Client 不得接受或转发 `file_path`。

- [ ] **Step 3: 固定 slice 与失效矩阵。**

  在 `projectWorkspaceViewModel.js` 实现：

  ```js
  export function createSlice() {
    return { data: null, loading: false, error: '' };
  }

  export const INVALIDATION = {
    project: ['list', 'overview', 'activity'],
    spatial: ['overview', 'assets', 'activity', 'mineOptions'],
    dataset: ['overview', 'assets', 'activity'],
    export: ['assets', 'exports', 'activity'],
    snapshot: ['assets', 'snapshots', 'activity'],
    restoreSnapshot: ['list', 'detail', 'overview', 'assets', 'activity', 'exports', 'snapshots', 'mineOptions'],
  };

  export const NEXT_ACTION_LABELS = {
    IMPORT_MINE_BOUNDARY: '导入矿山边界',
    CONFIGURE_BASEMAP: '配置空间资源',
    REGISTER_INFERENCE_INPUT: '登记推理影像',
    REVIEW_RESULT: '审阅成果',
  };

  export function formatActionLabel(actionCode) {
    return NEXT_ACTION_LABELS[actionCode] || actionCode || '未知操作';
  }

  export function toAssetRow(asset = {}) {
    return {
      id: asset.id || '',
      name: asset.name || '未命名资产',
      assetType: asset.asset_type || '',
      format: asset.format || '',
      status: asset.status || 'registered',
      version: asset.version ?? null,
      error: asset.error || null,
    };
  }
  ```

  在 `ProjectWorkspace.vue` 使用递增 `selectionRevision`；每个异步加载捕获开始时 revision，返回后只在 revision 等于当前值时写入状态。

- [ ] **Step 4: 将父组件改为一次装配和局部刷新。**

  保持 `App.vue` 的 `username`、`open-map(projectId, mineFid)`、`logout` 完全不变。把现有 `refreshCurrentProject()` 的“先 `selectProject` 再 `loadProjects`，又一次 select”改为按 `INVALIDATION` 刷新，避免重复请求。

  父组件负责：当前项目 ID、项目列表、slice、空间轮询、mutation 调度。子组件只接收 props 与 emit，不得拿到 `apiUrl` 或 axios。

- [ ] **Step 5: 验证纯 JS 层。**

  Run:

  ```powershell
  Set-Location D:\项目\YunNan\miner
  node --test test/projectWorkspaceApi.test.js test/projectWorkspaceViewModel.test.js test/projectWorkspaceHelpers.test.js
  ```

  Expected: action code、未知 action fallback、URL、错误态、过滤和过期响应保护全部通过。

- [ ] **Step 6: 提交数据流收口。**

  ```powershell
  git add miner/src/projectWorkspace/projectWorkspaceApi.js miner/src/projectWorkspace/projectWorkspaceViewModel.js miner/src/projectWorkspace/projectWorkspaceHelpers.js miner/src/components/ProjectWorkspace.vue miner/test/projectWorkspaceApi.test.js miner/test/projectWorkspaceViewModel.test.js miner/test/projectWorkspaceHelpers.test.js
  git commit -m "refactor(miner): 集中项目工作台数据装配"
  ```

### Task 8: 按领域职责拆分工作台视图并完成回归

**Files:**

- Create: `miner/src/components/projectWorkspace/ProjectSelector.vue`
- Create: `miner/src/components/projectWorkspace/ProjectForm.vue`
- Create: `miner/src/components/projectWorkspace/ProjectOverviewPanel.vue`
- Create: `miner/src/components/projectWorkspace/ProjectAssetsPanel.vue`
- Create: `miner/src/components/projectWorkspace/ProjectDatasetRegistrationPanel.vue`
- Create: `miner/src/components/projectWorkspace/ProjectActivityPanel.vue`
- Create: `miner/src/components/projectWorkspace/ProjectExportSnapshotPanel.vue`
- Create: `miner/src/components/projectWorkspace/ProjectSpatialResources.vue`
- Modify: `miner/src/components/ProjectWorkspace.vue`
- Modify: `miner/src/projectWorkspace/projectWorkspaceViewModel.js`
- Modify: `miner/test/projectWorkspaceViewModel.test.js`
- Modify: `docs/development_guide.md`

- [ ] **Step 1: 先创建无 HTTP 依赖的展示组件。**

  每个组件遵循以下边界：

  | 组件 | 必要 props | 必要 emits | 禁止项 |
  | --- | --- | --- | --- |
  | `ProjectSelector` | `items, selectedId, filters, loading, error` | `select, update:filters, reset, refresh, create` | 计算 readiness |
  | `ProjectOverviewPanel` | `overview, loading, error` | `edit, refresh, archive, restore, open-map, run-action` | axios、拼 URL、判断底图缺失 |
  | `ProjectAssetsPanel` | `items, loading, error, filters` | `update:filters, refresh, configure-spatial` | 显示路径、感知底层表 |
  | `ProjectDatasetRegistrationPanel` | `projectId, mineOptions, busy, error` | `register-dataset` | `file_path`、绝对路径、资产列表/任务轮询 |
  | `ProjectActivityPanel` | `items, loading, error` | `refresh` | 请求 timeline、存中文审计句子 |
  | `ProjectExportSnapshotPanel` | `exports, snapshots, capabilities, loading, error` | `create-export, create-snapshot, restore-snapshot` | `output_dir`、完整备份措辞 |
  | `ProjectSpatialResources` | `spatial, mineOptions, busy, error` | `preview-mine, import-mine, register-basemap, retry-job, cancel-job` | 读取 assets 底层结构 |

- [ ] **Step 2: 将空间向导单独移入 `ProjectSpatialResources.vue`。**

  现有空间向导有上传预览、字段映射、候选底图、启动切片、轮询进度等独立责任。把相关 template、props/emits、样式一次整体移走；父组件只保留请求与轮询。不要把它塞进 `ProjectAssetsPanel`，避免资产读取又承担导入、任务控制和轮询。

- [ ] **Step 3: 移除旧详情区的重复展示和不安全字段。**

  - 用 `ProjectOverviewPanel` 取代旧 summary card 和手工 `map_ready` 判断。
  - 用 `ProjectAssetsPanel` 取代旧数据集列表；不显示 `dataset.file_path`，也不根据 `source_type` / `source_id` 分支。
  - 用 `ProjectDatasetRegistrationPanel` 取代旧数据集登记表单；仅提交相对 `storage_key`，明确提示影像需由离线导入目录准备完成后才能登记。
  - 用 `ProjectActivityPanel` 取代旧时间线。
  - 用 `ProjectExportSnapshotPanel` 取代“导出与备份”；删除 `outputDir` ref 和“可选输出目录”输入框，将“备份”改为“项目配置快照”。
  - 使用 `ProjectForm` 保留建档/编辑字段；不改变旧项目 API 的字段名。

- [ ] **Step 4: 为展示映射添加纯函数回归。**

  在 `projectWorkspaceViewModel.test.js` 加入：

  ```js
  test('maps backend action codes without recalculating next actions', () => {
    assert.equal(formatActionLabel('CONFIGURE_BASEMAP'), '配置空间资源');
    assert.equal(formatActionLabel('UNKNOWN_ACTION'), 'UNKNOWN_ACTION');
  });

  test('does not expose physical path fields in asset presentation', () => {
    const row = toAssetRow({ id: 'dataset:1', name: '2024影像', file_path: 'D:/secret.tif' });
    assert.equal(Object.hasOwn(row, 'file_path'), false);
  });
  ```

  `toAssetRow` 只能读取公共 `ProjectAssetView` 字段；测试中额外路径字段必须被忽略。

- [ ] **Step 5: 编译和回归 Miner。**

  Run:

  ```powershell
  Set-Location D:\项目\YunNan\miner
  node --check server.js
  node --test test/projectWorkspaceApi.test.js test/projectWorkspaceViewModel.test.js test/projectWorkspaceHelpers.test.js test/projectRoutes.test.js
  npm test
  npm run build
  ```

  Expected: 所有 Node 测试和 Vite build 通过；`App.vue` 无需改动；浏览器手工验收项目切换、失败资产、空间任务失败、导出、配置快照恢复与错误态。

- [ ] **Step 6: 执行后端完整相关回归。**

  Run（固定 Python 3.10/GDAL 环境或 backend 运行镜像内执行）:

  ```powershell
  Set-Location D:\项目\YunNan\backend
  python -m unittest test_project_read_models.py test_project_api.py test_project_spatial.py test_project_map.py test_project_inference_results.py -v
  ```

  Expected: 项目、空间、地图隔离、推理成果发布和新 Read Model 全部通过；任何失败都按“原有问题/本次引入”分别记录。

- [ ] **Step 7: 同步文档、检查差异并提交。**

  在 `docs/development_guide.md` 增加上述后端运行环境说明、Miner 验证命令、`storage_key` 迁移说明、导出/配置快照语义。随后执行：

  ```powershell
  Set-Location D:\项目\YunNan
  git diff --check
  git status --short
  git add miner/src/components/ProjectWorkspace.vue miner/src/components/projectWorkspace miner/src/projectWorkspace docs/development_guide.md
  git commit -m "refactor(miner): 拆分低耦合项目工作台"
  ```

## 完工验收清单

- [ ] 根目录 `AGENTS.md` 仍是所有开发前必读入口，契约无重复或矛盾定义。
- [ ] `/overview` 与 `/assets` 的 fixture、Flask endpoint 和 BFF 响应逐字段一致。
- [ ] 前端没有业务层 readiness 判断、服务器物理路径或 `output_dir` 输入。
- [ ] 项目 `lifecycle_status`、readiness、asset status、job status 不混用。
- [ ] 列表/详情/空间/分类成果/导出/配置快照的既有项目能力未回归。
- [ ] 所有新导出和快照在项目 sandbox 内，并有可解释 manifest；UI 不再承诺完整数据备份。
- [ ] 现有与新增模块测试、Miner build 通过；Python 环境限制如实记录。
- [ ] 每个实现 PR 只含一个主模块，并按“契约 → Provider → Consumer → 集成验证”顺序合并。
