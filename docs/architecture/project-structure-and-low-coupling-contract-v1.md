# 项目结构与低耦合契约 v1

> 状态：设计冻结，尚未代表所有接口已经实现。
> 生效范围：YunNan 项目全部新增和修改；Project Hub 是首个落地模块。
> 规则入口：[根目录 AGENTS.md](../../AGENTS.md)。

## 1. 目标

本项目不建设通用项目管理系统。本期的架构目标是让项目工作台能够一致回答：

1. 项目当前处于哪个业务生命周期；
2. 当前工作流还缺哪些必要条件；
3. 项目有哪些空间、影像、成果和交付资产；
4. 哪些后台任务正在运行或失败；
5. 某项导出、成果或配置快照来自哪些输入和版本。

低耦合的验收标准不是“文件变多”，而是每个模块只拥有一种业务责任，下游只依赖公开 DTO/HTTP 契约，且可以在 mock 上游的情况下独立测试。

## 2. 当前事实与目标边界

当前 `ProjectWorkspace.vue` 同时承担项目列表、项目表单、空间资源向导、矿山展示、数据集登记、时间线、导出与备份，属于需要逐步拆分的协调层。后端已经有项目、空间资源、空间任务、数据集、活动、导出和备份记录。

本契约**不要求**立即迁移表结构、移动历史文件或替换已有 API。首期优先新增只读聚合模型，现有接口保持兼容；只有确实无法表达的新增业务事实才新增存储字段或表。

## 3. 领域边界

```text
Miner Web
  -> Miner BFF
    -> Flask Project API
      -> Project Hub（项目、概览、资产、活动）
      -> Spatial（矿山、底图、空间任务）
      -> Inference（固定模型任务、暂存输出）
      -> Results（正式成果、矢量修订、导出）
```

| 边界 | 拥有的数据/行为 | 公共输出 | 不得承担 |
| --- | --- | --- | --- |
| Project Hub | 项目生命周期、项目概览、资产 Read Model、活动审计 | `ProjectOverviewView`、`ProjectAssetView` | 模型加载、瓦片处理、直接读取前端路径 |
| Spatial | 矿山矢量、底图、切片、激活和空间任务 | 空间资源/任务 DTO、地图 manifest | 分类成果修订、项目业务状态决策 |
| Inference | 固定模型任务、能力检查、临时输出 | 任务 DTO、结果暂存引用 | 在线训练、正式成果发布 |
| Results | 结果发布、矢量 revision、来源追溯 | 成果/版本 DTO、可下载制品 | 修改原始影像、调用模型内部实现 |
| Miner BFF | 鉴权透传、同源路由和轻量适配 | 浏览器可消费 HTTP 响应 | 领域规则、数据库访问、路径扫描 |
| Miner Web | 当前项目上下文与用户交互 | 视图状态 | 计算项目准备条件、访问物理存储 |

## 4. 四层状态模型

### 4.1 项目生命周期

`lifecycle_status` 是项目唯一持久化业务状态：

```text
draft -> active -> completed -> archived
```

它表达项目管理层面的阶段，不表达资源是否齐备、任务是否运行或成果是否已审核。当前数据库字段 `Project.status` 是兼容期内的存储来源；首期不得为了名称统一而破坏现有 `status` API。

### 4.2 项目准备情况

`readiness` 是 Project Hub 在读取时计算的只读结果：

```text
blocked | partial | ready
```

首期采用可解释的检查项，不做没有业务依据的百分制或权重评分。建议固定五项：

| 检查 code | 通过条件 | 典型 blocker |
| --- | --- | --- |
| `PROJECT_PROFILE` | 项目名称与监测期有效 | `PROJECT_PROFILE_INCOMPLETE` |
| `MINE_BOUNDARY` | 存在可用矿山边界资源 | `NO_MINE_BOUNDARY` |
| `ACTIVE_BASEMAP` | 存在已激活且可读的项目底图 | `NO_ACTIVE_BASEMAP` |
| `INFERENCE_INPUT` | 存在符合推理输入契约的数据资源 | `NO_INFERENCE_INPUT` |
| `REVIEWABLE_RESULT` | 存在可审阅的成果或人工修订版本 | `NO_REVIEWABLE_RESULT` |

`passed/total` 只表示工作流覆盖度。是否可执行某个具体操作（如“启动推理”或“导出”）由后端返回对应 capability，不能用总分猜测，也不能让前端自行放宽条件。

### 4.3 资产状态

所有资产在对外聚合模型中使用：

```text
registered | processing | ready | failed | superseded
```

该状态属于单条资产。它允许底图处理失败而项目仍为 `active`，也允许旧版本底图为 `superseded` 而新版本继续使用。

### 4.4 任务状态

任务状态属于具体 Worker。当前空间任务已使用 `queued`、`running`、`succeeded`、`failed`、`cancelled` 等语义；v1 明确保留该词汇，**不**把它机械改为 `pending`。若未来需要统一展示，由 Read Model 映射展示标签，但原始任务 API 保持兼容。

## 5. ProjectOverviewView 契约

目标接口：`GET /api/projects/{project_id}/overview`。

```json
{
  "project_id": 42,
  "lifecycle_status": "active",
  "summary": {
    "name": "大理一期监测",
    "region": "大理州",
    "manager": "张三",
    "monitor_start_year": 2024,
    "monitor_end_year": 2025
  },
  "readiness": {
    "status": "partial",
    "passed": 3,
    "total": 5,
    "checks": [
      {"code": "MINE_BOUNDARY", "status": "passed", "reason_code": null},
      {"code": "ACTIVE_BASEMAP", "status": "blocked", "reason_code": "NO_ACTIVE_BASEMAP"}
    ]
  },
  "capabilities": {
    "can_configure_spatial": true,
    "can_start_inference": false,
    "can_review_result": false,
    "can_export": false
  },
  "blockers": [
    {"code": "NO_ACTIVE_BASEMAP", "severity": "warning"}
  ],
  "next_actions": [
    {"action_code": "CONFIGURE_BASEMAP", "target": "spatial_resource"}
  ],
  "counts": {
    "mines": 12,
    "assets": 8,
    "running_jobs": 1,
    "failed_assets": 0
  },
  "recent_activity": []
}
```

规则所有权：

- 后端决定检查项、blocker、capability 与 `next_actions` 的顺序；
- 前端只将 `action_code` 映射成当前 UI 的中文文案和路由；
- 不返回与 Vue 路由绑定的后端路径，以免领域层与页面结构耦合；
- `GET /api/projects/{id}` 和列表接口继续兼容，`overview` 作为新的聚合读取入口。

## 6. ProjectAssetView 契约

目标接口：

```text
GET /api/projects/{project_id}/assets
GET /api/projects/{project_id}/assets?type=imagery
GET /api/projects/{project_id}/assets?status=failed
```

首期固定 asset type：

```text
mine_boundary | basemap | imagery | inference_result |
vector_revision | report | export | backup_snapshot
```

统一读取 DTO：

```json
{
  "id": "basemap:17",
  "source_type": "project_spatial_resource",
  "source_id": 17,
  "asset_type": "basemap",
  "name": "2024-春季底图",
  "format": "tiff",
  "status": "ready",
  "version": 2,
  "created_at": "2026-09-04T09:00:00",
  "updated_at": "2026-09-04T10:00:00",
  "spatial": {"crs": "EPSG:4326", "bbox": [100.0, 25.0, 100.2, 25.2]},
  "temporal": {"start": "2024-03-01", "end": "2024-03-01"},
  "provenance": {"source": "offline_import", "parent_asset_ids": []},
  "error": {"code": null, "message": null},
  "capabilities": {"preview": true, "download": false, "activate": false, "retry": false}
}
```

`ProjectAssetView` 是稳定的**读取契约**，而不是立即新建的通用资产数据库表。v1 由适配器聚合现有 `ProjectSpatialResource`、`ProjectDataset`、成果记录、导出记录和配置快照；前端不得依赖这些底层表名或路径。

## 7. 工作流与前端数据流

工作流卡是能力检查与导航，不是一次性的强制状态机：

```text
项目建档
  -> 空间资源（Boundary / Basemap）
  -> 推理数据（Imagery / Dataset）
  -> 推理成果（Result / Vector）
  -> 审阅交付（Revision / Report / Export）
```

归档始终属于 `lifecycle_status`，不属于上述数据生产路径。重新登记影像、重新推理和重新审阅是合法循环。

前端目标拆分：

```text
ProjectWorkspace.vue              # 当前项目上下文和装配
ProjectSelector.vue               # 项目选择/筛选
ProjectOverview/
  ProjectSummary.vue
  ReadinessPanel.vue
  WorkflowSteps.vue
  NextActions.vue
ProjectSpatialResources.vue
ProjectAssets/
  AssetList.vue
  AssetFilters.vue
  AssetDetail.vue
ProjectActivity.vue
ProjectDataManagement/
  ExportPanel.vue
  SnapshotPanel.vue
```

拆分前先交付 `ProjectOverviewView` 与 `ProjectAssetView` fixture。子组件只接收公共 DTO 和事件回调；不得分别请求同一项目的概览数据，更不得各自复制就绪判断。

## 8. 活动记录与审计

数据库保存机器可读事件，UI 在展示层映射中文：

```json
{
  "action_code": "BASEMAP_ACTIVATED",
  "actor_id": "admin",
  "actor_type": "user",
  "target_type": "basemap",
  "target_id": "17",
  "result": "success",
  "job_id": null,
  "payload": {"previous_basemap_id": "16"},
  "created_at": "2026-09-04T10:00:00"
}
```

兼容期可复用现有 `ProjectActivityLog` 的 `event_type`、`actor` 和 `payload_json`；不要因为 v1 目标而一次性重写历史日志。新增事件须同时提供 action code、筛选语义和 UI 文案映射。

## 9. 项目存储、导出与快照

目标逻辑目录如下。它是新制品的约束，不授权为满足目录美观而搬迁已有大文件：

```text
PROJECT_STORAGE_ROOT/projects/{project_id}/
  raw/
  basemap/
  inference/
  revisions/
  exports/{export_id}/
  snapshots/
```

约束：

1. 客户端只能提交业务参数与可引用资产 ID，不能提交 `output_dir`、绝对路径或相对路径穿越片段。
2. 服务端由项目 ID 和导出 ID 生成目标目录，并用 allowlist/路径解析保证结果仍在项目 sandbox 内。
3. 每个导出写入 `manifest.json`，至少包含 `project_id`、创建时间、来源资产 ID、版本和可用校验值。
4. 页面把当前元数据备份称作“项目配置快照”；完整灾备仍由数据库、原始数据、瓦片和成果文件的运维恢复流程负责。

## 10. 实施顺序与门禁

| 阶段 | 交付物 | 完成门槛 |
| --- | --- | --- |
| P0 | 状态/资产 DTO、readiness 规则、action code、目录规则、fixture | 契约评审通过；无前端业务判断重复 |
| P1 | 后端 `overview`、`assets` Read Model | Flask 契约测试覆盖成功、无数据、失败资源 |
| P2 | 项目驾驶舱 | UI 仅消费 DTO；现有项目 CRUD 兼容 |
| P3 | 资产目录、活动、导出与快照语义 | 来源追溯与路径安全测试通过 |
| P4 | `ProjectWorkspace` 按职责拆分 | 不增加重复请求；构建与模块测试通过 |
| P5 | 跨模块回归与发布候选 | 小数据端到端链路、失败/恢复路径已验证 |

每次跨模块变更按下列顺序提交：

```text
契约和 fixture -> 提供方实现 -> 消费方实现 -> 集成验证
```

一个 PR 只拥有一个主模块；接口破坏性调整必须新增版本或兼容字段，不能静默替换。

## 11. 非目标

- 不引入 OpenProject、Plane、GeoNode 或 MapStore 的整套平台；仅借鉴其状态、资源与工作台设计思想。
- 不做在线训练、模型权重上传、React 重写、浏览器大影像上传/切片。
- 不做完整 RBAC、审批流、实时多人 GIS 编辑、工时或通用任务看板。
