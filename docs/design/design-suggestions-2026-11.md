# 设计建议（2026-11）

来源：2026-10-24 全面审查（docs/code-review-2026-10-24.md，9 P1 / 12 P2，批次 A–G 修复已全部入库）+ 真实大数据全功能验收（docs/real-data-acceptance-2026-11.md，R1–R7 与 §6 移交清单）。本文只收**尚未实现**的改进建议，每条 = 现状问题 → 建议方案 → 影响面 → 工作量（S ≤1 天 / M 数天 / L 数周）。已入库的修复（A–G、R2/R4/R6/R7）不再重复。

优先级速览：D1、D2、D6 为高（生产功能/数据风险），D3–D5、D7–D9 为中，其余低。

## 一、交互与工作流

### D1 编辑器绘制无 ROI 事前约束（验收实证，高）
- 现状：成果编辑器可对全地图绘制/移动几何，但保存校验要求 `roi.covers(geometry)`（容差仅 1e-9°）。验收实测：在 ROI 外空白区绘制三角形，前 8 步操作全部正常，保存时才 422「几何必须完全位于成果 ROI 内」（feature_index 精确指出越界要素）。用户完成整轮编辑后才发现部分劳动不可保存。
- 建议：编辑器加载成果时叠加 ROI 边界虚线层；绘制/顶点拖动落点越界时即时红显并在工具条提示；保存前置校验给出要素列表定位。后端校验保持不变（防线不放松）。
- 影响面：`ClassificationResultEditor.vue`（新增 ROI 叠加层与越界判定，纯前端）；后端成果详情已返回 `roi_geometry_json`，无需新 API。
- 工作量：M。

### D2 direct_select 下 trash 无效果且无提示（验收实证，高）
- 现状：mapbox-gl-draw 语义三特性——单击"已选中且唯一"的面进入 direct_select（顶点模式）；direct_select 下 trash = 删除激活顶点；3 顶点面受最少顶点保护，trash 点击**无任何反应**（验收首次实跑"删除面"步骤因此假通过，截图可见三角形残留）。simple_select 下拖动选中面 = 整面平移（易误移）。
- 建议：①direct_select 且选中面顶点数 ≤ 最小保护时，trash 点击弹提示「顶点数不足，无法删除顶点；请先退出顶点模式再删除整个面」；②整面平移后首次保存前，几何变化以 dirty 高亮 + 平移量提示；③退出顶点模式（Esc/点空白）的入口在工具条文案中说明。
- 影响面：`ClassificationResultEditor.vue`（draw 事件钩子上加提示文案）；无需后端改动。
- 工作量：S–M。

### D3 推理任务无 retry API（验收实证）
- 现状：任务失败后只能人工重建任务（`POST /jobs/:id/retry` 404；api/inference.py 仅 create/get/cancel/capabilities）。R1 宿主内存不足、R6 worker 重启等瞬时故障恢复后，用户需手工重填全部参数。底图切片已有 retry 且验收实测续传有效（§1 步骤 3），推理缺同等能力。
- 建议：新增 `POST /api/inference/jobs/<id>/retry`——复制原任务参数（project/dataset/year/mine_fids/device）创建新任务并返回新 id；源任务保持 failed 状态可追溯；限 queued/running 之外状态可 retry。
- 影响面：`api/inference.py` + `applications/inference_management.py`（复用切片 retry 的鉴权与审计模式）；前端任务行加「重试」按钮。
- 工作量：M。

### D4 影像上手绘后的工作流衔接
- 现状：上手绘（F 批次）→ 裁剪 → 切片 → 推理是一条五跳手动链（验收 §1 步骤 6→8 每步人工触发与轮询）。裁剪产物不会自动进入切片，切片产物不会自动建推理任务。
- 建议：不引入通用工作流引擎（本期明确不做），仅做两点：①裁剪成功后工具卡内嵌「切片此成果」直通按钮（预填 dataset_id）；②推理创建页支持从 dataset 下拉直选裁剪/切片产物并显示其来源链（来自哪张原片哪个多边形）。
- 影响面：`ImageryView.vue`（直通按钮）+ `SmartInterpretationView.vue`（来源链展示，字段已有 traceability 口径）。
- 工作量：M。

### D5 GB 级上传的进度与失败可解释性
- 现状：分片通道已实测可用（573M 暂停/续传/幂等复用全过），但单发通道 570s + 1.78GiB 内存峰值（R5），失败时用户只见通用错误；前端 >512MB 自动分流掩盖了单发风险，API 层 8GB 上限远超实际可承受（~2GB 即近 OOM）。
- 建议：①`MAX_UPLOAD_TIFF_SIZE_MB` 下调至 1GB 或单发路径改流式解析（werkzeug multipart 全量缓冲是根因）；②上传失败时展示已收分片与续传指引；③服务端在接近内存上限时提前拒绝并提示改用分片。
- 影响面：`api/file.py`（上限/流式）+ miner 上传组件（错误文案）。
- 工作量：M（流式）或 S（仅下调上限+文案）。

## 二、信息架构与 API 语义

### D6 trash 恢复无受控 API（验收实证）
- 现状：项目删除入 trash 后，恢复 = 手工 `mv trash/7_* projects/7` + `UPDATE project SET deleted_at=NULL` + `POST /restore`（验收步骤 15 演练成功，但要求运维直接操作 DB 与存储目录，无审计、易错、离线交付环境不可交给甲方）。
- 建议：`POST /api/projects/<id>/restore-from-trash`——服务端校验 trash 目录存在与键完整性后原子移回 + DB 复位 + 审计 `project_restored_from_trash`；UI 在项目列表「已删除」筛选下提供恢复按钮。trash 保留策略（自动过期清理）一并定义。
- 影响面：`applications/project_hub/service.py`（恢复事务与目录操作，可复用删除时的备份+回滚模式）+ 项目列表 API/UI。
- 工作量：M。

### D7 业务失败返回 HTTP 200 的 REST 语义
- 现状：`business_or_server_failure` 惯例把业务失败（项目不存在、越权等）以 HTTP 200 + `success:false` 返回（验收步骤 15：删除后详情 404 语义实际是 200）。前端逐处判 `success` 字段，监控/网关层看不到错误率。
- 建议：分两步收敛——新增接口一律用正确状态码（404/409/422）；存量接口在响应头加 `X-Business-Status` 过渡，前端统一在 request 封装层转换为异常，最终按模块迁移。
- 影响面：全后端 API（渐进式）；不一次性破坏兼容。
- 工作量：L（分模块摊销）。

### D8 数据集登记幂等与生命周期
- 现状：登记接口非幂等且无删除 API（验收步骤 4 脚本重跑产生重复登记 dataset 43/44，无法清理只能留档）；incoming/ 中同名文件重复登记产生多条记录指向同一物理文件。
- 建议：①登记支持 `idempotency_key`（或按 storage 键 + sha256 去重返回既有记录）；②删除 API 走 trash 模式（软删 + 目录入 trash）；③项目资产视图（ProjectAssetView）标注重复组。
- 影响面：`api/project.py` datasets 端点 + `project_hub`；与 D6 共享 trash 基建。
- 工作量：M。

## 三、数据链与可观测性

### D9 GPU/推理可观测性（R1 落地项）
- 现状：宿主 RAM 不足导致 `OSError: [Errno 12] Cannot allocate memory` 被误分类为 `GPU_INFERENCE_FAILED`，且 resolution 阶段丢弃原始异常；CPU 回退后模型常驻挤占内存，后续任务 GPU 永远失败（恶性循环，验收实测重启 worker 后 32s/2.7× 恢复）。
- 建议：①设备探测/加载失败落原始 traceback + 分类码；②Errno 12 单独分类 `HOST_MEMORY_EXHAUSTED`；③CPU 回退后空闲期卸载 CPU 模型择机重试 GPU；④部署文档标注推理机 RAM 底线（fp32 1.97GB checkpoint 加载需 ≥8GB，推荐 16GB）。
- 影响面：`inference_worker`（错误分类与模型生命周期）；文档。
- 工作量：M。

### D10 存量成果瓦片完整性巡检（R3 落地项）
- 现状：项目 1 激活底图 `tiles/2` 各 zoom 目录 0 张 PNG 但切片任务记录 succeeded——历史瓦片被清理或丢失后系统无感知，地图实际无底图。
- 建议：healthcheck 已覆盖进程级（G-2 已入库：/api/health SELECT 1 + storage 可写探针 + worker 心跳）。追加数据级巡检：低频任务（每周/启动时）对激活底图抽样 zoom 校验瓦片数 > 0，异常在项目概览 `blockers` 中呈现「激活底图瓦片缺失」。
- 影响面：`spatial_*` 模块 + readiness 计算挂接（后端统一计算原则不变）。
- 工作量：M。

### D11 验收/回归资产固化
- 现状：H-2/H-3 验收脚本与探针（D:/Temp/yunnan-h2/*.py、*.cjs）含大量一次性坐标与登录细节，散落临时目录；编辑器验收依赖"成果 62 水体面在 (830,650)"这类像素级脆弱坐标。
- 建议：将稳定可复用的部分收编进仓库 `tests/browser/`（登录/计数/保存 diff 断言工具化，坐标改为从 API bounds→屏幕换算），一次性探针保留在验收文档引用的临时目录即可。
- 影响面：测试基础设施；不改变现有 4 冒烟。
- 工作量：M。

## 四、明确不做（延续本期边界）

在线训练/权重上传、React 重写、通用项目平台、完整 RBAC/多人实时编辑/审批流——均维持 docs/fix-plan-20261024.md 的排除范围；上述建议不引入工作流引擎或插件机制。
