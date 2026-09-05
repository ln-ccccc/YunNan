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

`year` 可省略或留空，此时使用登记数据集的 `year_end`，没有结束年份时使用 `year_start`；均无有效年份时提示补充。后端还支持可选 `mine_fids`，当前 Miner 弹窗不传。创建成功返回 HTTP 201 的任务 DTO，随后通过 `GET /api/inference/jobs/{job_id}` 查询。

Miner 仍保留 `/api/inference/kml-roi` 路由别名，但它转发到相同的项目任务接口，不能再提交 `old_tif_path`、`new_tif_path`、`kml_path`、`old_year/new_year` 等旧请求字段。旧脚本的双年份参数不等于当前浏览器 API 契约。

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

趋势统计必须来自真实 `class_ratio_percent.json` 等结果文件；无数据时返回空表和覆盖率信息，不伪造全 0 趋势。

### 项目工作台存储迁移

- 运行时通过 `PROJECT_STORAGE_ROOT` 指定项目受控存储根；项目导出写入 `projects/{project_id}/exports/{export_id}/`，项目配置快照写入 `projects/{project_id}/snapshots/{snapshot_id}/`。
- `POST /api/projects/{project_id}/datasets` 不再接受浏览器传入的 `file_path`。仅登记已由离线导入流程放入 `incoming/` 的相对 `storage_key`，格式只能是 `.tif` 或 `.tiff`；绝对路径、`..` 片段和其他格式返回 `422`。成功仅返回 `id`、`asset_id`、`status` 登记回执；资产详情需通过 `/assets` 读取。
- 项目化地物分类使用 `POST /api/inference/jobs`。操作顺序是：将 TIF/TIFF 放入 `PROJECT_STORAGE_ROOT/incoming/`，在工作台登记为影像，待资产为 ready 后从项目或地图打开“开始地物分类”。浏览器只提交项目、影像数据集、可选年份和设备；不得填写服务器路径。年份未填写时使用影像登记年份。
- `POST /api/projects/{project_id}/exports` 与 `POST /api/projects/{project_id}/backups` 不接受 `output_dir`，传入时返回 `422`。导出请求只允许 `format` 和可选 `features`；GeoJSON/SHP 未提供 `features` 时由 Flask 根据当前项目矿山边界、绑定和数据集生成，BFF 只转发请求。即使是兼容的显式 `features`，服务端也会过滤保留路径键、覆盖 `project_id`，并只保留当前项目可验证的矿山/数据集引用。快照请求只允许 `scope=metadata_index`；其余字段或取值均返回 `422`。导出公开响应使用 `artifact_name`，快照公开响应使用“项目配置快照”名称；两者都不返回服务器物理路径。
- 恢复配置快照只接受与当前项目和快照记录精确匹配、状态为 `completed` 且 `restorable=true` 的受控 `manifest.json`。没有 `snapshot_version`、`project_id`、`backup_id` 身份字段的旧快照需要重新生成，不能直接恢复；成功响应为公开 `ProjectOverviewView`，不返回旧详情中的数据集路径。
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

## 6. 编码边界

- 优先复用现有目录和组件，不做无关目录重构。
- 不为“未来可能需要”新增配置中心、插件机制或抽象层。
- 不批量格式化无关文件。
- 修改 API、配置、命令或部署流程时必须同步更新文档。
- 错误信息至少说明失败原因、失败位置或下一步排查方向。

## 7. 测试分层

按 [开发规范](development-standard.md) 的测试矩阵选择现有 Node/Python 测试、对应前端构建和浏览器路径。项目化推理联调用登记影像与 `/api/inference/jobs`，矢量编辑联调使用 GeoView 的成果 API。旧 KML 上传只在修改其历史消费者时验证。

报告必须区分 mock 与真实运行、通过与跳过；构建不能替代浏览器操作，单元测试不能替代 GPU 和性能实测。

## 8. 交付要求

每次开发完成后必须列出：

- 改了什么。
- 为什么这样改。
- 实际执行的测试命令。
- 每条测试结果。
- 未验证项、原因与风险。

测试记录建议使用 [test_report_template.md](./test_report_template.md)。
