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

首期采用可解释的检查项，不做没有业务依据的百分制或权重评分。`total` 固定为 `5`，`checks` 必须按下表顺序返回五项，且每项只包含 `code`、`status`、`reason_code`：

| 检查 code | 通过条件 | 典型 blocker |
| --- | --- | --- |
| `PROJECT_PROFILE` | 项目名称与监测期有效 | `PROJECT_PROFILE_INCOMPLETE` |
| `MINE_BOUNDARY` | 存在可用矿山边界资源 | `NO_MINE_BOUNDARY` |
| `ACTIVE_BASEMAP` | 存在已激活且可读的项目底图 | `NO_ACTIVE_BASEMAP` |
| `INFERENCE_INPUT` | 存在已验证的 imagery 数据集，或当前激活底图拥有可读的受控原栅格；格式为 `tif`、`tiff`、`img` 或 `jp2` | `NO_INFERENCE_INPUT` |
| `REVIEWABLE_RESULT` | 任一 `ClassificationResult.vector_status` 为 `ready` 或 `ready_empty` | `NO_REVIEWABLE_RESULT` |

历史绝对 `file_path` 记录最多只能公开为 `registered` 资产，不能通过 `INFERENCE_INPUT` 检查；只有经验证的相对 `storage_key` 和受支持栅格格式（`tif`/`tiff`/`img`/`jp2`，GDAL 可读的单文件格式）组合才是可推理输入。

`passed/total` 只表示工作流覆盖度。`readiness` 独立于 `lifecycle_status` 计算，项目归档不改变五项检查的计算结果。是否可执行某个具体操作（如“启动推理”或“导出”）由后端返回对应 capability，不能用总分猜测，也不能让前端自行放宽条件。

### 4.3 资产状态

所有资产在对外聚合模型中使用：

```text
registered | processing | ready | failed | superseded
```

该状态属于单条资产。它允许底图处理失败而项目仍为 `active`，也允许旧版本底图为 `superseded` 而新版本继续使用。

对既有空间资源的公开映射固定为：`pending → registered`、`processing → processing`、`active → ready`、`failed → failed`、`retained → superseded`。该映射只影响 `ProjectAssetView.status`，不改变原始空间资源状态或项目生命周期。

### 4.4 任务状态

任务状态属于具体 Worker。当前空间任务已使用 `queued`、`running`、`succeeded`、`failed`、`cancelled` 等语义；v1 明确保留该词汇，**不**把它机械改为 `pending`。若未来需要统一展示，由 Read Model 映射展示标签，但原始任务 API 保持兼容。

## 5. ProjectOverviewView 契约

目标接口：`GET /api/projects/{project_id}/overview`。

HTTP 响应沿用标准 envelope：`{success, code, data}`。`data` 是完整的 `ProjectOverviewView`，必须包含 `project_id`、`lifecycle_status`、`summary`、`readiness`、`capabilities`、`blockers`、`next_actions`、`counts` 和 `recent_activity`，不得由页面额外拼接同一项目的状态。

```json
{
  "success": true,
  "code": 0,
  "data": {
    "project_id": 42,
    "lifecycle_status": "active",
    "summary": {
      "id": 42,
      "name": "合成项目示例",
      "region": "示例区域",
      "manager": "项目管理员",
      "remark": "仅用于契约说明。",
      "status": "active",
      "lifecycle_status": "active",
      "monitor_start_year": 2024,
      "monitor_end_year": 2025,
      "mine_count": 1,
      "dataset_count": 1,
      "spatial_status": "partial",
      "map_ready": false,
      "missing_resources": ["active_basemap", "reviewable_result"],
      "latest_activity_at": "2026-09-01T08:00:00Z",
      "create_time": "2026-09-01T08:00:00Z",
      "update_time": "2026-09-01T08:00:00Z"
    },
    "readiness": {
      "status": "partial",
      "passed": 3,
      "total": 5,
      "checks": [
        {"code": "PROJECT_PROFILE", "status": "passed", "reason_code": null},
        {"code": "MINE_BOUNDARY", "status": "passed", "reason_code": null},
        {"code": "ACTIVE_BASEMAP", "status": "blocked", "reason_code": "NO_ACTIVE_BASEMAP"},
        {"code": "INFERENCE_INPUT", "status": "passed", "reason_code": null},
        {"code": "REVIEWABLE_RESULT", "status": "blocked", "reason_code": "NO_REVIEWABLE_RESULT"}
      ]
    },
    "capabilities": {
      "can_open_map": false,
      "can_configure_spatial": true,
      "can_start_inference": false,
      "can_review_result": false,
      "can_export": false
    },
    "blockers": [
      {"code": "NO_ACTIVE_BASEMAP", "severity": "warning"},
      {"code": "NO_REVIEWABLE_RESULT", "severity": "warning"}
    ],
    "next_actions": [
      {"action_code": "CONFIGURE_BASEMAP", "target": "spatial_resource"},
      {"action_code": "REVIEW_RESULT", "target": "classification_result"}
    ],
    "counts": {
      "mines": 1,
      "assets": 2,
      "queued_jobs": 0,
      "running_jobs": 0,
      "failed_assets": 0
    },
    "recent_activity": []
  }
}
```

`summary` 复用现有 `ProjectSummary`，完整字段固定为 `id`、`name`、`region`、`manager`、`remark`、`status`、`lifecycle_status`（与 `status` 同值的语义化别名，AGENTS §4）、`monitor_start_year`、`monitor_end_year`、`mine_count`、`dataset_count`、`spatial_status`、`map_ready`、`missing_resources`、`latest_activity_at`、`create_time`、`update_time`。

`readiness` 固定为 `{status, passed, total, checks}`，其 `checks` 的顺序、代码和字段由第 4.2 节定义。`lifecycle_status` 与 `readiness` 是分离状态；归档项目仍照常计算 readiness。

`capabilities` 只能包含下列五项，全部由后端计算：

- `can_configure_spatial = lifecycle_status != archived`。
- `can_open_map` 要求存在可用矿山边界且存在已激活、可读的底图；归档项目若这两类资源仍在，允许只读打开地图。
- `can_start_inference = lifecycle_status != archived`，且 `PROJECT_PROFILE`、`MINE_BOUNDARY`、`ACTIVE_BASEMAP`、`INFERENCE_INPUT` 四项检查均通过。
- `can_review_result` 仅在任一 `ClassificationResult.vector_status` 为 `ready` 或 `ready_empty` 时为真。
- `can_export = can_review_result`。

`blockers` 的元素形状固定为 `{code, severity}`。`next_actions` 由后端按检查顺序输出领域 `{action_code, target}`；它不得返回 Vue 路由、页面标签或其他前端实现细节。`counts` 必须且只能包含 `mines`、`assets`、`queued_jobs`、`running_jobs`、`failed_assets`；其中 `queued_jobs` 单列原始任务状态 `queued`，`running_jobs` 只统计原始任务状态 `running`。

规则所有权：

- 后端决定检查项、blocker、capability 与 `next_actions` 的顺序；
- 前端只将 `action_code` 映射成当前 UI 的中文文案和路由；
- 不返回与 Vue 路由绑定的后端路径，以免领域层与页面结构耦合；
- `GET /api/projects/{id}` 和列表接口继续兼容，`overview` 作为新的聚合读取入口。

### 矿山导入面积单位

`POST /api/projects/{id}/spatial/mines` 增加可选 `area_unit`，只接受 `m2`、`km2`，省略时按旧接口的 `m2` 处理。Miner 在选择面积字段后必须显式选择源单位；不从字段名称猜测单位。后端将绑定的 `area_snapshot` 统一为 m²，并在规范化 GeoJSON 添加同单位的 `area_m2`。原文件和原面积属性保持原值；页面详情和项目统计优先消费 `area_m2`，旧记录仍兼容既有字段。地图 GeoJSON 保留原面积属性及 `area_m2`；项目导出沿用各格式既有字段，本次不新增面积列。未映射/缺失面积保留为空，不声称自动计算多边形面积；非有限或负面积、未知单位明确拒绝。活动记录保留本次源单位。成功、缺失、参数错误与失败样例见 `tests/fixtures/project-hub-v1/mine-area-units.json`。

已有错误单位的项目不会静默重算，需要选择正确源单位重新导入矿山；该操作产生新的空间资源版本，不覆盖原文件。

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

每个公开 asset 只能拥有 DTO 示例中列出的字段：`id`、`source_type`、`source_id`、`asset_type`、`name`、`format`、`status`、`version`、`created_at`、`updated_at`、`spatial`、`temporal`、`provenance`、`error`、`capabilities`。`source_type` 与 `source_id` 仅是不透明的溯源标识；Web 不得据此对底层表名分支、拼接 URL 或访问物理路径。公开 DTO 绝不可返回 `file_path`、`source_path`、`normalized_path`、`tile_path`、`manifest_path` 或 `output_dir`。

嵌套 `capabilities` 的固定键为 `preview`、`download`、`activate`、`retry`；v1 不增加 `restore` 或其他未冻结键。既有空间资源状态映射统一按第 4.3 节执行。

`POST /datasets` 的成功响应是安全登记回执，仅含 `id`、`asset_id` 和 `status`；页面随后通过 `/assets` 读取公开资产。配置快照恢复成功返回 `ProjectOverviewView`，不得复用旧项目详情 DTO。历史 `GET /api/projects/{project_id}` 为兼容接口，可能仍保留旧字段；新 Miner 工作台不得调用它或把其响应转发给浏览器。GeoJSON/SHP 导出未传 `features` 时，由 Flask 从当前公开矿山边界、项目绑定和数据集生成导出要素，BFF 只转发请求且不得读取旧详情；兼容的显式 `features` 同样必须过滤保留路径键、覆盖 `project_id`，并校验矿山/数据集引用只属于当前项目。

项目化地物分类统一调用 `POST /api/inference/jobs`。Miner 只提交 `project_id`、`dataset_id`、可选 `year` 和 `device`；后端确认数据集属于当前项目、是已就绪的 `imagery`，再解析受控 `incoming/` 或本项目 `projects/{id}/inputs/imagery/` 下受支持栅格文件、矿山边界和项目输出目录。浏览器不得提交或接收推理输入、KML、输出目录等服务器物理路径。省略 `year` 时使用影像登记年份；两者都没有时，后端直接提示补充年份。裁剪/切片产物继续使用既有目录，保存相对 storage key，继承登记来源年份和矿山引用；readiness 与推理使用相同目录规则。兼容详情的 `datasets[].file_path` 只返回受控相对键，不能安全映射的历史物理路径返回 `null`。

激活底图原栅格同时通过 `/assets?type=imagery&status=ready` 暴露为 `id=basemap_imagery:{resource_id}` 的 imagery 读取视图；固定资产 DTO 不增加字段，`source_type=spatial_resource`，`source_id=resource_id`，`provenance.source=active_basemap_source`，`parent_asset_ids=[basemap:{resource_id}]`。它与 basemap 视图描述同一资源的两个用途，不新增文件或数据集；`counts.assets` 仍统计公开读取视图，`dataset_count` 不变；影像裁剪候选清单不重复新增底图。仅当前最高版本的 active 底图可以提供该视图，原源文件必须位于 `incoming/` 或本项目 `basemap/`、`basemaps/` 下，使用受支持格式并有可读栅格元数据及 CRS。缺源、不可读、越界或非激活资源不满足 `INFERENCE_INPUT`，XYZ/PNG 瓦片永远不是模型输入。

推理请求增加 `input_asset_id`（直接使用公开 imagery 资产 ID），与旧 `dataset_id` 必须且只能提供一项。`imagery:{id}` 延续数据集规则；`basemap_imagery:{id}` 由后端重新确认项目归属、当前激活资源及受控原源文件，必须显式提交四位 `year`，私有 Worker 参数和公开任务请求保留 `basemap_resource_id` 来源。Miner 不按 `source_type` 或服务器目录做选择。任务使用原始栅格像素，沿用已有预处理链；切换激活底图后旧资产 ID 返回 400，不会自动改用新底图。成功、无源、非法/跨项目、不可读与未激活样例见 `tests/fixtures/project-hub-v1/active-basemap-inference.json`。

影像候选 `GET /api/projects/{id}/imagery/candidates` 的各项增加可空 `bounds`：EPSG:4326 `[west,south,east,north]`，从该影像实际地理参考转换，不使用固定区域。缺少 CRS、文件或有效栅格时返回 `null`，尺寸等不可读摘要同样为空。成功、空、不可读示例见 `tests/fixtures/project-hub-v1/imagery-candidates.json`。裁剪前确认原始 Polygon/MultiPolygon 有效且面积大于零，退化或自交范围返回 400，不通过缓冲修复。裁剪仍输出请求范围的矩形像素窗口：米制外扩在以范围中心为原点的 WGS84 局部等距投影计算，然后投回影像 CRS，窗口向外取整并限制在源影像内；不改为多边形掩膜、不降采样。产物保留 NoData 和有效数据掩膜。

空间矿山预览和公开 GeoJSON 输出可以保留用户定义的业务属性，但必须递归剔除属性名为 `file_path`、`source_path`、`normalized_path`、`tile_path`、`manifest_path`、`output_dir` 的字段（大小写不敏感）。这些是存储保留名，不能作为可映射的矿山字段，也不能作为浏览器可读属性输出。导入时必须将用户所选 FID 映射写入规范化 GeoJSON 的 `FID_1`，使后续地图、矿山选择和自动导出不依赖临时字段映射。

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

数据库保存机器可读事件，UI 在展示层映射中文。`GET /api/projects/{project_id}/timeline` 对当前和历史记录统一输出下列时间线 DTO：

```json
{
  "id": 17,
  "event_type": "spatial_job_queued",
  "action_code": "SPATIAL_JOB_QUEUED",
  "actor": "admin",
  "target": {"type": "spatial_job", "id": "uuid"},
  "result": "success",
  "payload": {"resource_id": 16},
  "created_at": "2026-09-04T10:00:00",
  "timestamp": "2026-09-04T10:00:00"
}
```

`ProjectOverviewView.recent_activity` 在兼容期维持紧凑审计 DTO：`action_code`、`actor_id`、`actor_type`、`target_type`、`target_id`、`result`、`job_id`、`payload`、`created_at`。读取器同时兼容旧的结构化 payload 和当前的 `event_type + actor + target + result` 记录；不合格或包含物理路径的历史 payload 必须整条忽略或脱敏，不能传给浏览器。

兼容期可复用现有 `ProjectActivityLog` 的 `event_type`、`actor` 和 `payload_json`；不要因为 v1 目标而一次性重写历史日志。新增事件须同时提供稳定事件类型、action code 映射、目标类型/ID 和 UI 文案映射。

## 9. 项目存储、导出与快照

目标逻辑目录如下。它是新制品的约束，不授权为满足目录美观而搬迁已有大文件：

```text
PROJECT_STORAGE_ROOT/projects/{project_id}/
  raw/
  basemap/
  inference/
  revisions/
  exports/{export_id}/
  snapshots/{snapshot_id}/
```

约束：

1. 客户端只能提交业务参数与可引用资产 ID，不能提交 `output_dir`、绝对路径或相对路径穿越片段。v1 的导出请求仅允许 `format` 和可选 `features`；对于 GeoJSON/SHP，未传 `features` 时由导出服务从当前项目资源生成，配置快照仅允许 `scope=metadata_index`；其他字段或值一律拒绝。
2. 服务端由项目 ID、导出 ID 或快照 ID 生成目标目录，并用 allowlist/路径解析保证结果仍在项目 sandbox 内。目录的每个既有层级以及制品、清单文件本身都不得是 symlink、junction 或其他 reparse point；不得仅因目标仍位于总存储根下而跨项目跟随链接。
3. 每个导出写入 `manifest.json`，至少包含 `project_id`、创建时间、来源资产 ID、版本和可用校验值。
4. 每个项目配置快照写入 `snapshots/{snapshot_id}/manifest.json`，清单必须包含 `snapshot_version`、`project_id` 和 `backup_id`。恢复仅接受与当前项目和快照记录精确匹配、状态为 completed 且 restorable 的普通清单文件；旧的无身份字段清单需要重新生成快照后才能恢复。
5. 页面把当前元数据备份称作“项目配置快照”；完整灾备仍由数据库、原始数据、瓦片和成果文件的运维恢复流程负责。
6. 配置快照的数据集引用保持相对 storage key；导入/恢复允许 `incoming/` 和目标项目自身的相对引用，拒绝绝对客户端路径、目录遍历及跨项目引用。快照只保存索引，不复制影像；引用文件缺失时资产仍为 registered。安全映射到相同 allowlist 的历史服务端记录在新快照中规范化为相对键。无法安全映射的历史路径或空文件索引在清单中保存为 `file_path: null`，保留其名称、类别、年份等配置元数据；导入/恢复以空字符串写入既有非空路径列，保持未就绪，不能用于推理，不改变数据库结构。经身份和受控文件校验的旧服务端快照恢复同样将不可公开引用归一为空索引；客户端导入非空的外部路径仍拒绝。

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
