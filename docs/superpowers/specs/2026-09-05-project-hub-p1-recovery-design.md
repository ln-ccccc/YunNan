# Project Hub P1 恢复与安全推理设计

日期：2026-09-05  
状态：用户已确认开始修复  
关联契约：`docs/architecture/project-structure-and-low-coupling-contract-v1.md`、`docs/superpowers/specs/2026-08-20-project-aware-interpretation-routing-design.md`

## 1. 背景与目标

一次真实的 Project Hub 浏览器验收确认了三个会中断主流程的 P1：

1. 可解析的矿山 GeoJSON 若唯一整数字段不叫 `FID_1`，预览会在用户能够选择字段前失败；
2. 已登记的项目影像保存为受控相对 `storage_key`，但旧推理入口仍要求浏览器提交服务器绝对路径，因此无法启动任务；
3. 底图空间任务失败或取消后，空间向导停留在进度页，无法改选候选底图；即使换图成功，旧失败资源也可能继续把空间状态标为失败。

本设计只恢复这三条路径。目标是让用户能完成“导入矿山边界 → 配置底图 → 登记影像 → 从项目/地图发起推理”的可验证链路，同时保持模型、Worker 算法、存储目录和 GeoView 矢量编辑边界不变。

## 2. 已确认约束

- 浏览器、Vue 组件和 BFF 不得提交、拼接或显示物理路径、`storage_key`、KML 路径和输出目录。
- 项目工作台只使用公开 DTO；项目规则、输入解析和 readiness 门禁由 Flask 持有。
- `ProjectDataset.file_path` 继续仅在服务端保存已验证的相对 `incoming/*.tif` 或 `incoming/*.tiff` 键；不做数据库迁移。
- 推理 Worker 继续消费其私有的绝对路径任务载荷；本次不改模型、权重、在线训练、算法参数或 Worker 协议。
- 旧 GeoView 的 `/api/analysis/kml_roi_inference` 上传目录流程不在本次范围；Miner 的项目化入口不再走路径型请求。
- 历史失败空间任务不能删除，因为它们是审计记录；它们也不能持续阻塞当前资源重新配置。

## 3. 方案比较与选择

### 方案 A（采用）：复用任务接口，服务端从受控资产 ID 解析输入

浏览器只向 `POST /api/inference/jobs` 提交：

```json
{
  "project_id": 12,
  "dataset_id": 34,
  "year": "2024",
  "mine_fids": [101],
  "device": "auto"
}
```

Flask 验证项目、readiness、数据集归属、受控 TIFF 键、文件存在性和矿山绑定，再内部生成 KML、输出目录以及供 Worker 使用的绝对路径。HTTP 响应永久脱敏 `request`、`result` 和 `error` 中的路径字段。

优点是只保留一个任务/状态/Worker 生命周期，避免 Project Hub 再创造平行的推理任务模型；缺点是当前路径型 Miner 请求需要与前端一起升级，并明确返回 422 迁移提示。

### 方案 B：新建 `/api/projects/{id}/inference-jobs`

该端点可以保持现有 `/api/inference/jobs` 不变，但会把同一个任务创建规则分散到两个 HTTP 入口，旧接口仍会向浏览器泄露路径。它增加了 BFF、测试和长期维护面，因此不采用。

### 方案 C：允许现有接口继续接受路径，并只修复相对键解析

该方案改动最小，却继续允许浏览器传入服务器路径，也无法保证响应不泄露路径，直接违反项目级低耦合与安全契约，因此不采用。

## 4. 设计

### 4.1 矿山预览与 FID 导入

`preview_mine_vector()` 只负责解析文件结构、几何、字段、样本和建议映射；它不再因为无法自动选出 FID 而拒绝预览。返回 DTO 新增向后兼容字段：

```json
"suggested_fid_validation": {
  "status": "valid | needs_selection | invalid",
  "message": "可为空的诊断文案"
}
```

- `valid`：建议 FID 已通过唯一正整数校验；
- `needs_selection`：没有自动候选，用户须在已解析字段中选一个；
- `invalid`：存在建议字段但其值不合格，用户可选择其他字段。

实际写入边界仍是 `import_mine_vector()`：它对用户选择的 FID 做唯一正整数校验，并继续规范写入公开 `FID_1`。前端预览成功后总是显示字段选择器；诊断仅为非阻塞提示，未选择 FID 时导入按钮仍禁用。

### 4.2 底图失败后的恢复

空间向导只将 `queued`、`running` 任务视为进行中，二者显示进度页并轮询。`failed`、`cancelled` 为终态：在缺少活动底图时回到候选底图页，显示最近失败原因和“重试原任务”按钮，但用户可立即选择另一候选图。

步骤判定提取为纯函数 `resolveSpatialWizardStep(spatial)`，规则固定为：

```text
存在 queued/running                      → 步骤 4（处理进度）
缺少 mine_vector                         → 步骤 2（矿山边界）
缺少 basemap 且没有进行中任务            → 步骤 3（候选底图）
其余                                     → 步骤 4（完成/当前状态）
```

空间状态服务的优先级同时调整为：

```text
存在 pending/processing                  → processing
无 missing_resources                     → ready
仍缺失的资源类型存在 failed              → failed
其余                                     → unconfigured
```

因此旧失败底图仍可审计，但新底图激活后 `map_ready=true` 与 `spatial_status=ready` 保持一致。

### 4.3 受控影像发起推理

新增一个只供后端使用的输入解析单元 `project_hub/inference_inputs.py`。它接收 `project_id` 与 `dataset_id`，并完成：

1. 验证项目存在、未归档且 `can_start_inference=true`；
2. 验证数据集属于该项目、类型为 `imagery`、格式/相对键为合法的 `incoming/*.tif(f)`；
3. 使用项目存储根解析真实文件，并拒绝缺失文件、目录、绝对键和越界键；
4. 读取当前活动矿山资源与项目绑定 FID，生成仅内部使用的 KML 路径、输出根和允许的 FID 范围；
5. 对可选 `mine_fids` 做项目绑定与空间范围交集校验；
6. 调用既有 `normalize_job_request()` 与 `create_job()`，让持久化任务继续携带 Worker 所需的私有绝对路径。

Flask 接口只接受白名单字段 `project_id`、`dataset_id`、`year`、`mine_fids`、`device`；任何 `old_tif_path`、`new_tif_path`、`kml_path`、`output_root`、`storage_key`、`file_path`、`limit` 或未知字段一律返回 422，且不创建任务。任务公开序列化只包含安全的任务元数据和安全请求字段，并递归移除路径键，防止轮询响应或错误消息泄露路径。

Miner 工作台通过后端 `can_start_inference` 展示“开始地物分类”。该动作复用已有项目地图和同一推理弹窗，而不创建第二套启动器：工作台导航到当前项目地图并携带一次性打开标志；地图按钮与自动打开均使用同一个重构后的 `InferenceModal`。弹窗按项目公开资产接口加载可用影像，显示名称和年份，提交 `dataset_id`，不显示或接收服务器路径。

## 5. 模块边界

| 模块 | 本次负责 | 明确不负责 |
| --- | --- | --- |
| `project_hub/inference_inputs.py` | 受控资产解析、项目/矿山边界校验 | 调用模型、修改 Worker 状态 |
| `api/inference.py` | 安全 HTTP 白名单、创建已有任务 | 浏览器路径兼容、GIS 算法实现 |
| `inference/jobs.py` | 私有任务载荷持久化、公共 DTO 脱敏 | 项目数据集查询 |
| Miner BFF | 同源透明转发与兼容错误返回 | 路径解析、readiness 判断 |
| Project Workspace | 消费 `can_start_inference` 并导航 | 推导前置条件、选择物理路径 |
| Map / InferenceModal | 影像资产选择、提交安全 DTO、展示任务 | 直接调用存储、构造 KML |
| 空间资源组件 | 终态任务恢复展示 | 删除历史任务、重新计算空间状态 |

## 6. 失败处理与兼容性

- FID 预览的成功不代表可导入；最终导入仍可能因选择的列重复、非整数或越界而返回明确 4xx。
- 失败空间任务继续展示错误与重试入口；换图不删除旧任务或旧资源。
- 旧的路径型 `/api/inference/jobs`/`/api/inference/kml-roi` 请求返回 422，并提示使用项目内已登记影像；Miner 前端与该变更同步升级。
- 旧 GeoView 上传端点不变，不能据此宣称全系统的历史路径型流程已迁移。
- Worker 若与后端不共享项目存储挂载，任务会由 Worker 失败；本次测试使用相同的 `PROJECT_STORAGE_ROOT`，部署验收须验证 Compose 挂载一致。

## 7. 测试与验收

后端：

- 自定义 `mine_code` 预览返回 `needs_selection`，选择后成功导入并生成 `FID_1`；重复自动建议字段的预览为 `invalid`，但导入仍负责最终拒绝；
- 失败/取消底图任务返回候选步骤；旧失败底图后激活新底图，空间状态为 `ready`；
- 合法 `dataset_id` 能创建任务，数据库私有任务有真实路径但 HTTP DTO 及轮询完全无路径；
- 跨项目数据集、归档项目、未满足 readiness、路径键、未知键、非法/不存在相对键、无效 FID 都被拒绝且不创建任务。

前端与 BFF：

- 纯步骤解析覆盖运行中、失败、取消、缺矿山、缺底图与完成状态；
- API client/弹窗请求只发送安全字段；BFF 保留 Cookie 并不重写业务数据；
- 从工作台触发和从地图触发都打开同一推理弹窗；无可用影像时给出明确提示。

集成：

```text
mine_code GeoJSON 预览 → 选择字段并导入
→ 底图任务失败 → 改选新候选 → 激活成功
→ 登记 incoming/*.tif → 选择影像启动任务
→ Worker 读取私有载荷 → 发布项目分类/矢量成果
→ Overview 变为可审阅，浏览器网络响应不含物理路径
```

本次本地验收可用 mock/轻量 Worker 验证任务创建与结果发布；完整模型运行、离线镜像和共享挂载作为部署包验收项单独记录。

## 8. 非目标

- 在线训练、权重上传、精度承诺、模型重训；
- React 改造、浏览器 GB 级影像上传/切片；
- GeoView 矢量编辑功能重写、多人实时编辑、完整 RBAC；
- 历史 GeoView 上传路径的全量迁移；
- 新的通用任务平台、数据集版本系统或数据库迁移。
