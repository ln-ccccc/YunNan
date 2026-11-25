# 云南矿山监测项目开发文档与规范

> 本文档提供开发命令和存量接口说明。每次开发或修改前，必须先阅读项目级 [AGENTS.md](../AGENTS.md)；项目结构、状态模型、模块边界和跨模块契约以 [项目结构与低耦合契约 v1](./architecture/project-structure-and-low-coupling-contract-v1.md) 为准。

后续开发的性能目标、按模块测试、两人分工和 PR 要求统一见 [开发规范](development-standard.md)；新人任务指令与交接示例见 [协作手册](agent-collaboration-guide.md)。

## 1. 项目结构

- `miner/`：矿山监测主系统，包含 Vite/Vue 前端与 `server.js` 提供的 Miner API。
- `backend/`：Python/Flask 与遥感解译、KML ROI、光谱指数等算法服务。
- `frontend/`：旧 GeoView 前端。除非任务明确要求，不在 miner 优化任务中重构该目录。
- `docs/`：开发、部署、测试与运行文档。

## 2. 服务与端口

- Miner Web：`miner` 的 Vite 开发服务，默认 `http://localhost:4000/`。
- Miner API：`miner/server.js`，默认 `http://localhost:8000/`。
- 后端 Flask：以 `backend` 现有启动脚本为准，供 GeoView 或算法服务调用。

如端口被占用，应优先改本地启动参数，不要直接修改接口契约或硬编码端口。

## 3. 常用命令

```powershell
cd miner
npm install
npm test
node --check server.js
npm run build
```

```powershell
cd frontend
npm install
npm run build
```

```powershell
cd backend
python -m unittest test_spectral_indices.py
python -m unittest test_new_features.py
```

## 4. Miner API 契约

### `POST /api/kml/upload`

这是仍保留的旧客户端上传接口，返回旧式服务器路径。新项目化地物分类入口不调用它；不要把下面的路径示例用于项目推理请求。

Request:

```json
{
  "filename": "example.kml",
  "content": "<kml>...</kml>"
}
```

Success:

```json
{
  "kml_path": "D:\\项目\\YunNan\\miner\\uploads\\kml\\example.kml"
}
```

Failure:

- 非 `.kml` 文件：`400`
- 空内容：`400`
- 保存失败：`400`

错误响应应包含可读的 `error`，并尽量提供 `next` 排查建议。

### `POST /api/inference/jobs`

当前 Miner 项目化分类入口经 BFF 转发到 Flask 同名接口，提交示例：

```json
{"project_id": 7, "dataset_id": 12, "year": "2024", "device": "auto"}
```

### `POST /api/projects/{id}/spatial/basemap-upload`

底图网页上传（2026-10-31）：浏览器以 `application/octet-stream` 流式直传，BFF 只做
鉴权（authGuard）与流式转发；**校验与落盘在后端**（`spatial_service.receive_basemap_upload`），
落盘 `incoming/<文件名>` 后即成空间资源面板的底图候选。文件名经 query 传递并
`encodeURIComponent`。

- Query：`filename=<原始文件名>`（`encodeURIComponent` 编码）
- Body：`application/octet-stream`（原始文件字节流，不使用 multipart）
- Success：`{"code":0,"data":{"filename":"x.tif","size_bytes":...,"candidate":"incoming/x.tif"}}`
- Failure：
  - 非 TIF/TIFF、IMG、JPG/PNG、ENVI(.dat)：`200` + `success:false` 信封（`不支持的底图格式`）
  - 文件名非法/路径穿越：`200` + `success:false`（`文件名非法`）
  - `incoming/` 同名已存在：`409`（`请重命名后重试`）
  - 超过 15GB：`413`（`底图文件超过 15GB 上限`）
  - 未登录：`401`

约束：扩展名白名单与重名/上限校验以**后端为单一真源**（BFF 零校验零落盘，
`project_storage` 对 BFF 保持只读）；落盘使用 `.staging` 原子转正，与底图候选
列表的 `_is_incomplete_copy_name` 契约对齐。

### 底图分片续传通道（>64MB 自动分流，总上限 100GB）

`POST /api/projects/{id}/spatial/basemap-upload/init`（JSON）：`upload_key`
（文件名+大小+修改时间的摘要，同一文件断点续传）、`filename`、`total_size`、
`chunk_size`。返回 `session_id/chunk_size/received[]`（已收分块直接跳过）。

`POST /api/projects/{id}/spatial/basemap-upload/chunk/{session_id}/{index}`
（octet-stream，头 `X-Chunk-Sha256` 由 BFF **原样透传**——丢头即静默跳过内容
校验）：分块大小不符/校验和不匹配 `400`。

`POST /api/projects/{id}/spatial/basemap-upload/complete/{session_id}`（JSON
`filename`）：合并落盘 `incoming/<filename>`。错误：分块缺失 `400`；重名
`409`；done 幂等——同名重试返回上次结果，**改名重试 `409`**（不得返回指向
不存在文件的结果）。**属主校验**贯穿三端点：会话非本人 init/chunk/complete
一律 `400`（`无权操作他人的上传会话`）。

### `POST /api/projects/{id}/spatial/basemap-tileset-register`

甲方预切 XYZ 瓦片包（`incoming/` 下 `{z}/{x}/{y}.png` 目录）直挂登记：JSON
`{"candidate": "incoming/<目录名>"}`，探测结构后原子拷贝到规范 `tile_path`
并**复用切片通道的 `_activate_basemap` 激活状态机**（互斥下线旧 active、
`.active` 标记 commit 成功后才删旧、保留最近 2 份历史、版本号
`func.max(version)+1`）。错误：非 XYZ 目录 `200`+`success:false`；归档项目
`200`+`success:false`（`归档项目不允许修改空间资源`）。bounds 按 Web Mercator
标准 `atan(sinh(…))` 反算。

### `GET /api/projects/{id}/imagery/preview?source=<storage_key>&max_size=2048`

原始影像高保真预览：实时降采样 tif 原件为 PNG，返回 `{png_base64}`；
`source` 限定受控根并集（本项目 inputs ∪ 全局 incoming）。前端预览弹层须做
请求竞态守卫（切换矿山后旧响应作废，不得写入当前弹层）。

### `GET /api/mines/trend-report`

Query:

- `class_name=forest|grassland|building|road|bareground|water`
- `direction=upward|downward|stable|all`

Response 至少包含：

- `mine_total`
- `coverage`
- `available_classes`
- `filters`
- `class_trends.selected_class`
- `tables.selected_class_rows`

趋势统计必须来自真实 `class_ratio_percent.json` 等结果文件；无数据时返回空表和覆盖率信息，不伪造全 0 趋势。地类面积的口径（2026-11-24）：未修订年份 = 推理占比 × 矿山面积；人工修订过的年份 = 图斑绝对面积（占比的分母是"已绘图斑"，不能拿它乘整矿面积）。

### 分类成果派生统计（2026-11-24 按派生数据性质重构）

- 「同矿山同年哪条成果有效」只有一条规则（`classification_stats.select_effective_result_refs`）：同年取 id 最大者，`vector_failed` 的成果在同一年存在非 failed 兄弟时让位。溯源、变化矩阵、趋势、补算全部共用，不得另写一份。
- 逐修订的地类占比/面积：保存修订时写出 `projects/{project_id}/outputs/classification-results/{result_id}/revisions/{revision_no}.stats.json`（`percent`、`area_m2`、`feature_count`，带 `result_id`/`revision_no`）。文件不可变、按（成果, 版本号）寻址；缺失时读端按当前矢量现算同一个纯函数。`class_ratio_percent.json` 只属于推理发布，任何保存都不再改写它——旧文件里残留的 `revised` 块不再被任何读端采信。
- 变化矩阵：`outputs/change_matrix/{fid}.json` 是整矿派生缓存，另有一份重算回执 `.{fid}.rebuild.json`（记录针对哪组有效成果、结果如何、矩阵内容摘要）。重算不在保存请求里同步等待（进程内后台队列，幂等）；重算期间若又有新修订落地，本次结果丢弃重算。
- `GET /api/projects/{project_id}/mines/{fid}/traceability`：
  - `years[].class_ratio_percent`：修订年份取自修订统计（`class_ratio_area_source=vector_shoelace`），未修订年份取推理占比（`inference_mask`）；人工清空的年份为全 0。
  - `ratio_series.area_source`：序列里两种口径混有时为 `mixed`。
  - `change_matrix_state`：`revised`（已按人工修订重算）/ `inference`（矩阵涉及的年份没有人工修订，推理矩阵即正确口径）/ `revised_empty`（有一期图斑为空，无转移数据）/ `unavailable`（不足两期、缺 label 等）/ `pending`（待重算；本次读取会补一次重算请求，稍后刷新即可）。`change_matrix_note` 是给用户看的说明。
- 保存一条已被更新成果取代的历史成果，只改变该成果自己的修订与统计，不影响任何按矿山聚合的视图（溯源、矩阵、趋势只认有效成果）。
- 历史数据补齐：`python backfill_class_ratio_revised.py`（幂等；只补 sidecar 与重算矩阵，不改业务数据）。新保存会自动完成这两件事，平时不需要跑。

### 底图重新激活（2026-11-24）

- 切片被取消的资源落 `failed`（原因写明"瓦片不完整，可重新提交断点续切"），不再标成 `retained`，因此不会出现在历史底图列表里。
- `GET /api/projects/{project_id}/spatial/basemaps/retained` 每条附 `reactivable` 与 `reactivate_blocked_reason`。
- `POST /api/projects/{project_id}/spatial/basemaps/{resource_id}/reactivate` 只接受同时满足三条件的底图：状态为 `retained`、最近一次切片任务 `succeeded`、声明的每个缩放级别都有瓦片；否则 `400` 并说明原因。归档项目 `400`。
- `.active` 标记切换顺序与切片 worker 一致：写新标记 → 提交数据库 → 成功后才删旧标记（提交失败时旧标记不动）。
- 资源回收（激活新底图时执行）：同项目超额的 `retained` 保留最近 2 个；`failed` 的切片保留最近 1 个供重试断点续切（`retry_spatial_job` 复用同一资源，gdal2tiles 带 `--resume`），更早的连同源文件副本、瓦片目录、任务行一并删除，并记 `spatial_resource_removed`（`reason=failed_tiling_pruned`）。正在重试的资源（`pending`/`processing`）不在清理范围内。此前 failed 的半成品没有任何清理方。

### 导出（2026-11-24）

- 同项目同格式已有一条仍在生成的 `pending` 导出时，`POST /api/projects/{project_id}/exports` 返回 `422`（"…导出正在生成中，请稍后在导出列表查看并下载，不要重复提交"）。
- 后端单次台账生成的时间预算为 4 分钟（`EXPORT_TIME_BUDGET_SECONDS`），超时把记录落 `failed` 并清理目录；Miner BFF 对创建导出的出站超时为 10 分钟。后端一定先于链路上任何一跳给出明确结果。预算对两条大 JSON 抽取同样生效：MySQL 语句带 `MAX_EXECUTION_TIME`（剩余毫秒数），语句被数据库中断（错误码 3024）时按预算超时处理，不重试——此前只在写表循环里检查，而耗时几乎全在抽取上。
- 台账的两项抽取（逐任务的推理计时、逐成果的类别计数）走项目派生缓存：只抽取新增或有变化的任务与成果。项目 10 实测整本工作簿由约 13 秒降到约 1.4 秒，两种方式的结果逐格相等。
- `manifest.json`：`xlsx` 台账附 `classification_results`（台账实际读取的每条成果的 `result_id`、`revision_no`、`vector_status`、`mine_resource_version`）；`geojson/shp/dxf` 附 `mine_vector`（构成几何的矿山边界资源版本）。
- Miner BFF：路由参数校验失败一律 `400`（此前兜成 `502`），且不向上游发请求。

### 项目派生缓存（2026-11-24）

- 约定与签名在 `backend/applications/project_hub/derived_cache.py`。从大 JSON 派生、源数据几乎不变的值，按"源是否变过"记在 `projects/{project_id}/outputs/stats/` 下，一个缓存一个文件：概览的 `class_area_by_result.json`，台账的 `ledger_job_timings.json` 与 `ledger_class_counts.json`。签名没变直接取用，变了的只重算那几条。
- 签名只决定要不要重算：成果 = 修订号 + 图斑数 + 矢量状态 + 行更新时间；任务 = 状态 + 完成时间 + 行更新时间。新增这类缓存时，参与签名的列必须是源数据所有写入路径都会改动的列。
- 缓存是派生数据：读不回来、格式不对、版本不同一律当空，删掉任何文件下一次读取只是慢一次；写入原子替换，写失败只记告警。文件只保留当前仍然有效的条目。
- 测试环境（`TESTING`）下与概览的 TTL 缓存同样旁路，用例之间不共享；需要验证缓存逻辑的测试显式打开（`build_stats_overview(..., use_area_memo=True)`、`build_project_ledger_workbook(..., use_cache=True)`）。

### Miner BFF 出站请求（2026-11-24）

- BFF 访问 Flask 后端的全部请求经 `miner/services/backendFetch.js`（`fetchBackend`），每个请求 `Connection: close`，不复用连接。新增出站调用必须走它，不得直接 `fetch`（守护：`miner/test/backendFetch.test.js`）。
- 原因：gunicorn 未设 `--keep-alive`（默认空闲 2 秒关闭连接）且不发 `Keep-Alive` 超时提示；Node 18 自带 undici 的连接池默认保留空闲连接 4 秒。复用到服务端正在关闭的连接会得到 `UND_ERR_SOCKET`（other side closed），在浏览器里是随机的 502（"登录校验服务不可用"或"上游服务不可用"）。线上日志保留范围内 38 次（会话校验 9、接口转发 29）。
- 实测：间隔约 2 秒的只读请求，修复前 240 次失败 27 次；修复后三轮共 720 次 0 失败。代价是每次上游请求多约 0.6ms（容器内微基准：复用 p50 约 1.2ms，不复用约 1.8ms）。
- 会话校验对连接类瞬态错误（`ECONNRESET`/`ECONNREFUSED`/`ETIMEDOUT`/`EPIPE`/`UND_ERR_SOCKET`）重试一次，间隔 300ms；接口转发不重试（哪些 POST 可以安全重放未逐个确认）。
- 若后端调整 keep-alive（发超时提示或调大 `--keep-alive`），可重新评估恢复连接池；那属于部署变更，需负责人决定。

### 项目工作台存储迁移

- 运行时通过 `PROJECT_STORAGE_ROOT` 指定项目受控存储根；项目导出写入 `projects/{project_id}/exports/{export_id}/`，项目配置快照写入 `projects/{project_id}/snapshots/{snapshot_id}/`。
- `POST /api/projects/{project_id}/datasets` 不再接受浏览器传入的 `file_path`。仅登记已由离线导入流程放入 `incoming/` 的相对 `storage_key`，格式只能是 `.tif` 或 `.tiff`；绝对路径、`..` 片段和其他格式返回 `422`。成功仅返回 `id`、`asset_id`、`status` 登记回执；资产详情需通过 `/assets` 读取。
- 项目化地物分类使用 `POST /api/inference/jobs`。操作顺序是：将 TIF/TIFF 放入 `PROJECT_STORAGE_ROOT/incoming/`，在工作台登记为影像，待资产为 ready 后从项目或地图打开“开始地物分类”。浏览器只提交项目、影像数据集、可选年份和设备；不得填写服务器路径。年份未填写时使用影像登记年份。
- `POST /api/projects/{project_id}/exports` 与 `POST /api/projects/{project_id}/backups` 不接受 `output_dir`，传入时返回 `422`。导出请求只允许 `format` 和可选 `features`；GeoJSON/SHP 未提供 `features` 时由 Flask 根据当前项目矿山边界、绑定和数据集生成，BFF 只转发请求。即使是兼容的显式 `features`，服务端也会过滤保留路径键、覆盖 `project_id`，并只保留当前项目可验证的矿山/数据集引用。快照请求只允许 `scope=metadata_index`；其余字段或取值均返回 `422`。导出公开响应使用 `artifact_name`，快照公开响应使用“项目配置快照”名称；两者都不返回服务器物理路径。
- 恢复配置快照只接受与当前项目和快照记录精确匹配、状态为 `completed` 且 `restorable=true` 的受控 `manifest.json`。没有 `snapshot_version`、`project_id`、`backup_id` 身份字段的旧快照需要重新生成，不能直接恢复；成功响应为公开 `ProjectOverviewView`，不返回旧详情中的数据集路径。
- 恢复按记录身份原位更新（2026-11 问题3/4 契约）：datasets/mines 不删表重建，自增 ID 保持稳定——任务载荷里的 `dataset_id` 引用与 `exports/{export_id}/` 物理目录在恢复后仍然有效。快照后新增且未被推理任务引用的数据集登记按恢复语义移除；被任务引用的保留并在 `backup_restored` 事件 payload 的 `retained_referenced_datasets` 中留痕。导出记录与活动时间线是不可变历史，恢复只追加 `backup_restored` 事件，不删除也不重放旧活动。manifest 内 datasets 段缺少记录 `id` 的旧版快照不做猜测关联：数据集段跳过恢复（事件 payload `skipped_sections` 留痕），summary/mines 段照常恢复。
- 跨环境导入（`POST /api/projects/{project_id}/backups/import`）始终为数据集生成新身份，并在 `project_imported` 事件 payload 与 `projects/{project_id}/imports/manifest_*.json` 存档中记录 `dataset_id_map`（源 ID→新 ID）与 `retained_dataset_ids`；目标项目被推理任务引用的既有登记不随导入删除。旧环境快照中的数据集 ID 不得视为新环境有效引用。
- `GET /api/projects/{project_id}/overview` 和 `GET /api/projects/{project_id}/assets` 是 Project Hub 的公开只读聚合入口；浏览器经 Miner BFF 调用，不能根据底层表名或存储路径自行推断资源状态。
- 旧 `GET /api/projects/{project_id}` 仅用于兼容历史客户端；新 Miner 工作台不得调用或转发其详情响应，BFF 也不得为导出读取该旧详情。
- 矿山矢量预览和 `GET /api/projects/{project_id}/geojson` 会保留普通业务属性，但会递归过滤属性名为 `file_path`、`source_path`、`normalized_path`、`tile_path`、`manifest_path`、`output_dir` 的字段（大小写不敏感）；禁止把这些保留名当作矿山字段映射。导入时会把用户选定的 FID 映射规范为公开 `FID_1`，供地图、资产和自动导出使用。

### 项目工作台验证

Project Hub 改动需要同时验证 BFF、浏览器侧数据装配和后端 Read Model。Miner 侧执行：

```powershell
cd miner
node --check routes/projects.js
node --check services/projectBackend.js
node --test test/projectWorkspaceApi.test.js test/projectWorkspaceViewModel.test.js test/projectWorkspaceHelpers.test.js test/projectRoutes.test.js test/projectBackend.test.js
npm test
npm run build
```

后端测试必须使用项目固定的 Python 3.10/GDAL 运行环境（或对应运行镜像），避免将宿主机缺失 Flask/GDAL 依赖误判为代码问题：

```powershell
cd backend
python -m unittest test_project_api test_project_read_models test_project_spatial -v
```

## 5. 前端规范

- 主界面文案默认使用中文，按钮文案必须与实际行为一致，例如导出 CSV 时写“导出 CSV”。
- 页面级失败必须可见：数据加载失败、趋势统计失败、推理失败不能静默降级为 0 数据。
- 删除矿山等高风险入口只有在后端接口真实支持并经过验证后才允许展示。
- 地图首次加载可自动缩放到全量边界；筛选刷新不应反复打断用户视角，搜索命中时只定位目标矿山。
- 把 blob 存成本地文件统一经 `saveBlobAsFile`（Miner `src/utils/blobDownload.js`，GeoView `src/utils/blobDownload.mjs`）：下载发起 60 秒后才释放 object URL，同步释放会截断尚未开始的下载。下载入口不得自己 `createObjectURL`/`revokeObjectURL`（守护：两侧的 `test/blobDownload.test.*`）；预览图等非下载用途除外。
- 盖在地图上的纯文字提示不拦截鼠标（`pointer-events: none`）：被它盖住的那块地图上点击画不出顶点、选不中图斑，且没有任何反馈。
- 同一接口的请求在途时不发第二个；首屏不要两个组件各拉一次同一接口。`/api/stats/overview` 在 120 秒缓存过期后要重算（重算走项目派生缓存，见下文"项目派生缓存"，2026-11-24 实测约 0.5 秒；此前约 9 秒），重算期间同一 worker 上的其它接口会被拖慢。

## 6. 编码边界

- 优先复用现有目录和组件，不做无关目录重构。
- 不为“未来可能需要”新增配置中心、插件机制或抽象层。
- 不批量格式化无关文件。
- 修改 API、配置、命令或部署流程时必须同步更新文档。
- 错误信息至少说明失败原因、失败位置或下一步排查方向。

## 7. 测试分层

按 [开发规范](development-standard.md) 的测试矩阵选择现有 Node/Python 测试、对应前端构建和浏览器路径。项目化推理联调用登记影像与 `/api/inference/jobs`，矢量编辑联调使用 GeoView 的成果 API。旧 KML 上传只在修改其历史消费者时验证。

报告必须区分 mock 与真实运行、通过与跳过；构建不能替代浏览器操作，单元测试不能替代 GPU 和性能实测。

夹具式真实浏览器回归放在 `tests/browser/`（说明见该目录 README）：HTTP 夹具只在测试浏览器内拦截，不登录真实账号、不写真实项目。只有真实浏览器才能发现的问题（消息组件渲染、浮层吞点击等）修完后要沉淀为其中的脚本，并对修复前的构建跑出红灯。

## 8. 交付要求

每次开发完成后必须列出：

- 改了什么。
- 为什么这样改。
- 实际执行的测试命令。
- 每条测试结果。
- 未验证项、原因与风险。

测试记录建议使用 [test_report_template.md](./test_report_template.md)。
