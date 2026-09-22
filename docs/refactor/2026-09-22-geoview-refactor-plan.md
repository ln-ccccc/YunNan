# 解译平台（GeoView）前后端重构计划 2026-09-22

## 1. 背景与目标

旧版解译平台小 bug 不断、偶发大 bug（2026-09-20 全量审计 P1×9/P2×20 已修，但结构性病根仍在：巨型单体视图、前后端各处复制粘贴、错误处理两套实现、推理不可取消）。本计划参考姊妹项目江西平台（D:\项目\JiangXi\JiangXi-Platform）已验证的调整，对云南解译平台做一次**有边界的结构重构**。

- 解译平台范围 = `frontend/`（GeoView）+ Flask 后端中 GeoView 消费的 API 面（`api/analysis|file|history|model|inference` + `kml_roi/` + 公共工具）。
- **不含**：`miner/`（独立应用）、推理运行时与模型链路、部署系统、`project_hub/service.py` 拆分（项目域，服务 Miner，另行治理）。
- 遵循 [AGENTS.md](../../AGENTS.md)：正确性/可验证性/兼容性优先；不改跨模块 HTTP 契约（本轮零 API 变更）；每阶段可独立验证、独立提交。

## 2. 江西可借鉴项 → 云南落点

| # | 江西做法（证据） | 云南落点 |
| --- | --- | --- |
| 1 | Segmentation 拆 4 步骤子组件，provide/inject 保状态归属（Segmentation.vue:78 provide `seg`） | 云南两视图拆分（P3），并进一步：两视图重复的上传卡抽成**共享组件**（江西没有此问题所以没做） |
| 2 | 轮询/取消/断线自愈收进 `api/upload.js`（kmlRoiInfer 内置轮询、kmlRoiCancel、sleepWithAbort、断链阈值常量） | 轮询从 `utils/getUploadImg.js` 迁入 API 层 + 接通云南后端已有的 cancel 端点（P4） |
| 3 | request.js 错误 `kind` 分类（auth/backend/http/network） | 云南 request.js 补 kind，`isDisconnectError` 从字符串猜测升级为精确判定（P1） |
| 4 | NotFound.vue 仅 131 行 | 云南 1612 行（~1520 行死 CSS）瘦身（P1） |
| 5 | 后端 `common/utils/safe_paths.py` 统一路径防御 | 新建云南同款模块，收敛 kml_roi/analysis 分散校验（P5） |
| 6 | analysis.py 组织法：私有辅助前置 + 路由按 submit→status→cancel→history 链排序 | 云南 `api/analysis.py`（465 行）重组（P5） |
| 7 | 契约测试分套脚本（test:workflow/test:download/test:ui） | 云南 package.json 分套（P6） |
| 8 | kml_roi jobs/runs 文件态实体化 | **不借鉴**：云南推理作业已 DB 实体化（InferenceJob + worker should_cancel 钩子，jobs.py:294/worker.py:243），架构优于文件态，无需改 |

江西侧已知的坏的部分不照搬：SpectralIndices 未拆分（云南要拆）、analysis.py 业务逻辑留路由层（云南下沉）、AGENTS 584 行镜像谱系文档（不仿）。

## 3. 已核实的关键事实（2026-09-22 本机复核）

- `views/history/History.vue`（570 行）、`components/DraggableItem.vue`（109 行）零外部引用，死代码。
- `api/requestfile.js` 仅 `api/upload.js createSrc` 一个调用点；其 HTTP 错误分支静默 reject（request.js 已修未同步）——两套拦截器行为已分叉。
- 后端 `POST /api/inference/jobs/<id>/cancel`（inference.py:126）+ worker `should_cancel` 钩子（worker.py:243-248 会真正终止并落 cancelled 终态）全链路可用，**前端从未调用**——推理最长 1 小时无取消入口、无进度反馈。
- `Segmentation.vue` 与 `SpectralIndices.vue` 重复实现整套文件选择栈（isValidTiff/normalizeSelectedItems/createUploadItems/readDroppedItems/walkFileTree ≈120 行），且 isValidTiff 已出现大小写处理漂移。
- `_read_model_success_api` 同时返回 `code=SUCCESS`，与前端 `code!==0` 拦截器兼容（SUCCESS=0 时），无需动契约（P5 中核对 SUCCESS 值即可）。

## 4. 分阶段执行（OCR 分包思想：每包独立可验证、独立提交）

按 [审查方法论](../code-review/methodology.md) 的分包原则组织；每阶段 = 一个提交（中文提交信息，直接提交 main，不 push）。

### P1 死代码清理 + HTTP 基础设施统一（前端）
- `NotFound.vue` 1612→~100 行（删死 CSS，保留 404 功能与跳转）。
- 删除 `History.vue`、`DraggableItem.vue`。
- `requestfile.js` 并入 `request.js`：`createSrc` 改走 `request({silent:true})`（上传已有自己的进度通知，不叠全屏锁），删除分叉副本。
- `request.js` 错误打 `kind`（auth/backend/http/network）；`isDisconnectError` 优先读 kind。
- `Home.vue` `window.onresize` 卸载泄漏修复。
- 验证：`node --test` + `npm run build` + GUI 巡检（404 页、上传、轮询 silent 不锁屏）。

### P2 上传栈去重（前端）
- 新建 `utils/tiffSelection.mjs` 纯函数模块：isValidTiff（统一小写判定）/normalizeSelectedItems/createUploadItems/readDroppedItems/walkFileTree + 单测（node --test）。
- 两个视图改用共享模块，删各自副本；行为不变（大小写统一为小写超集，等价）。
- 验证：新单测 + `node --test` + build + GUI 上传/拖拽/文件夹递归。

### P3 视图组件化（前端）
- 共享组件 `components/TiffUploadCard.vue`（dropzone+文件夹/文件选择+拖拽递归+清空，props/emits，两视图复用）。
- `Segmentation.vue` 拆：上传卡（共享组件）+ 预处理参数区 + 预览/历史区（provide/inject 保状态归属，江西模式）；裁剪器联动（setPreviewFile/disableCutForBatchUpload）留在父级响应 select 事件。
- `SpectralIndices.vue` 拆：上传卡（共享组件）+ 参数表单 + 历史。
- `ClassificationResultEditor.vue` **只做轻量抽取**（如内嵌类别色表外提），不动地图生命周期（fa71c0c 刚修的初始化竞态 + 源码顺序守护测试必须继续绿）。
- 验证：build + 既有 6 个测试全绿 + GUI 巡检（两页全流程 + 编辑器地图渲染）。

### P4 推理取消闭环 + 轮询收编 API 层（前端功能补齐）
- `waitForKmlRoiJob`、终态集合、断链阈值常量迁入 `api/`（新建 `api/inference.js`），`getUploadImg.js` 改为消费 API 层。
- 新增 `kmlRoiCancel(jobId)` 调 `/api/inference/jobs/<id>/cancel`。
- 推理等待期加常驻通知（第 i/n 张、当前任务号、**取消推理**按钮）→ 取消后轮询见 cancelled 终态即退出，文案明确"任务已取消"。
- 验证：API 层纯函数单测 + GUI 真实推理（CPU fallback 可测）+ 取消实测。

### P5 后端 safe_paths 收敛 + analysis.py 重组
- 新建 `applications/common/utils/safe_paths.py`：受控根内解析、正整数标识校验、输出目录白名单；迁移 kml_roi/analysis 路由分散的路径校验（先盘点调用点，逐个迁移，行为等价）。
- `api/analysis.py` 重组：私有辅助前置、路由按 image_pre→semantic→spectral→kml_roi submit/status/cancel→history 链排序（纯移动，零 API 变更）。
- 核对 `SUCCESS` 常量 = 0（包络兼容性结论落文档）。
- 验证：backend 容器 pytest 全量（基线 363 passed）+ 新增 safe_paths 单测。

### P6 测试分套 + 文档沉淀
- `package.json` 分套：`test`（全量）/`test:api`/`test:workflow`（按 P2-P4 新增测试归类）。
- testing-playbook 沉淀本轮新技巧；本计划文档补执行结果。

## 5. 验收（三套技能，写码与审查分离）

1. **全量回归门**：backend 容器 pytest（权威跑法见 testing-playbook）≥基线全绿；frontend `node --test` 全绿 + `npm run build` 零错误；真实浏览器 GUI 巡检（按 playbook 技巧16：先断言 visibilityState）。
2. **open-code-review（委托模式）**：对全部改动文件跑 `ocr delegate rule` 取规则组，逐文件按规则复核；改动 diff 跑 `ocr delegate preview` 确认审查面完整。
3. **agent-skills 五轴**：正确性/安全/性能/可维护性/测试，每轴对改动面过一遍，severity 分级（P0-P3），逐条 file:line 证据。
4. **Zhong-s_Skills high-intensity-testing**（已 clone 至临时目录）：跑 `scripts/review-preflight.py` 预检 → 六步工作法（并行子代理多遍：契约遍/数据流遍/git 考古遍/对抗遍，任务书注入项目契约+历史教训地雷图）→ P0/P1 主控亲自 file:line 复现 → 发现固化成测试。技能自带测试（test_*.py）先跑一遍确认其在本地可用。

## 6. 风险与守护

- 编辑器地图生命周期零改动；其源码顺序守护测试（fa71c0c）是硬门。
- 上传/推理行为变更点仅两处（requestfile 合并、取消闭环），其余均为行为等价重构；GUI 实测覆盖这两处。
- 后端零 API 契约变更；safe_paths 迁移逐调用点等价验证。
- 不碰 git 未跟踪的 `miner/*`、`docs/images/refresh/*`（非本轮产物）。
- 若 GUI 实测环境不可用，明确记录"未测项"，不声称已验证。

## 7. 执行结果（2026-09-22 落地记录）

| 阶段 | 提交 | 结果 |
| --- | --- | --- |
| P1 死代码+HTTP 基础设施 | c495ac3 | 完成：NotFound 1612→90 行；删 History.vue/DraggableItem.vue；requestfile 并入 request（kind=auth/backend/http/network/aborted）；onresize/overflow 泄漏修复。测试 21/21 |
| P2 上传栈去重 | abb8840 | 完成：tiffSelection.mjs + 5 组单测；两视图 -149 行；isValidTiff 漂移统一。测试 26/26 |
| P3 视图组件化 | 3e14946 | 完成：TiffUploadCard 共享组件（v-model+select 事件）；Segmentation 724→483、SpectralIndices 365→200；编辑器色表外提 classificationColors.mjs（地图生命周期零动，守护测试绿）。测试 29/29 |
| P4 推理取消闭环 | 6cea1ee | 完成：api/inference.js + inferencePolling.mjs（fetchJob 注入，7 组单测）；推理常驻通知+取消按钮接通后端 cancel；silent 补全（不再每秒弹错误 toast）。测试 36/36 |
| P5 后端 | 无代码改动 | **核实后取消预设改动**：①analysis.py 已是"辅助前置+链路排序"组织，纯重排=化妆性 churn；②路径防御三原语（inference/paths.resolve_output_file、spatial_storage.resolve_storage_path、analysis._resolve_spectral_kml_path）各归其域、契约不同、零重复，合并成 safe_paths.py 无去重收益；③SUCCESS=0 已核实，`success=True` 与 `code` 并存完全兼容前端 `code!==0` 拦截器，无契约问题。GeoView 后端消费面的实质修复已在 2026-09-20 审计轮完成 |
| P6 测试分套 | 本提交 | 完成：package.json 增 test:api（backendUrl/uploadGuards/inferencePolling）与 test:workflow（tiffSelection/classificationColors/三个 context/editor） |

### 规模与质量变化

- 前端 src：死代码 -2300 行（NotFound 1522 + History 570 + DraggableItem 109 + requestfile 50 + 视图去重），新增可测纯模块 4 个（tiffSelection/classificationColors/inferencePolling + api/inference）。
- 单测 6 个文件 21 例 → 10 个文件 36 例。
- GeoView 两大主视图从 798/440 行单体 → 483/200 行 + 269 行共享上传卡。

### 未测项与后续

- P7 验收（backend 容器回归、真实浏览器 GUI 巡检、OCR+五轴+high-intensity-testing 三套技能）结果见验收记录一节（验收完成后补写）。

## 8. P7 验收记录（2026-09-22）

### 回归门

| 门 | 结果 |
| --- | --- |
| backend 容器全量 pytest（主树挂载，权威跑法） | **363 passed / 0 failed / 10 skipped / 149 subtests**（修复轮后复跑，与基线持平；含新增 vector_path 白名单断言） |
| frontend node --test | **37/37**（基线 21 例 → 37 例） |
| frontend npm run build | 零错误 |
| 真实浏览器 GUI（localhost:3000 实栈，playbook 技巧16 visibilityState 先行） | 见下 |

### GUI 实测记录（真实栈：主树 dist 由容器静态服务）

登录→路由守卫✓；地物分类页 TiffUploadCard 全要素渲染✓；页面内 fetch 真实 tif 字节+DataTransfer 注入选择✓（"已选择 1 个"）；standalone 通道（无项目上下文）：上传→同步推理→"Pro 推理完成"→通知正常关闭✓；项目态 job 通道（?project_id=1 + e2e_drill.tif）：路由 201 建任务→轮询打点→取消按钮点击→"已请求取消"→**"已取消推理，未生成结果"终态文案**✓（后端 worker 真终止，DB 落 cancelled）；上传失败反馈（坏字节 tif）：错误 toast 弹出且单条✓；断链（回归容器抢 CPU 致请求夭折）："连接已中断"分支✓；常驻通知 close 修复：build B 实测两通知正常消失✓。
未完成项：最终 build（IIFE toast 守卫增量）的 standalone 复验被 **Docker Desktop 宿主机端口代理假死**阻断（容器内服务健康、宿主 000，环境故障与本轮代码无关）；该增量仅 5 行 toast 守卫，其行为已在前一 build 逐路径验证。

### 三套技能验收

1. **open-code-review（委托模式）**：`ocr delegate rule` 对 GeoView 后端消费面（analysis/inference/file.py）取 Python 规则组核验；改动面规则注入三路审查任务书。
2. **agent-skills 五轴**（对抗遍子代理，写码/审查分离）：正确性轴不通过（P1 上传反馈链断裂）→ 已修；安全轴通过（无 v-html/路径消费/XSS/原型污染面）；性能轴通过（1 条 P3 备注）；可维护性基本通过（死导出已清）；测试轴补 kind 分支/rgb 元素/record_source 断言。
3. **Zhong-s_Skills high-intensity-testing**：技能自带测试 9/9 绿；review-preflight.py 预检正常（OCR 探测失败时降级 git-fallback 符合设计）；六步工作法落地为三路独立上下文并行审查（契约遍/数据流遍/对抗遍），P0/P1 主控逐条 file:line 复核后修复，发现固化成测试。

### 审查发现与修复（三遍交叉命中标注 ◎=双遍以上）

| 级别 | 发现 | 修复 |
| --- | --- | --- |
| P1 ◎ | 上传失败零反馈：silent 灭 toast + 包装错误无 .response 使 catch 分支恒死（连带裁剪器/预处理上传全哑） | createRequestError 保留 response + 错误带 silent 标记 + getUploadImg/SpectralIndices/MyVueCropper/preHandle catch 补分支（silent!==false 守卫避免与拦截器双弹） |
| P2 ◎ | 多 tiff 取消状态不重臂：取消命中已终态任务（后端 200 空操作）后按钮永久失效 | 新任务创建时重臂 cancelRequested/activeJobId |
| P2 | buildProjectInferenceCards 无 record_source → 删除落 flash 误删端点弹"记录不存在" | 补 record_source:'project' + 测试 |
| P2 | kml_roi_inference 201 响应泄露服务器绝对路径 vector_path | 后端响应白名单（mode/project_id/matched_fids/warnings/job）+ 双向断言（注：运行中 gunicorn 需重启才载入，测试已覆盖） |
| P1(GUI) | element-plus@2.1.10 常驻通知 handle.close() 实测不生效（上传/推理通知滞留屏幕） | persistentNotification 包装：句柄.close→exposed.close→DOM 摘除兜底，GUI 实测消失 |
| P3 ◎ | 轮询把 kind=backend/auth 确定性失败当断线重试 120 次 | 立即抛出 + 测试 |
| P3 | classColor rgb 元素非数字产出非法 CSS | every(Number.isFinite) + 测试 |
| P3 | api/inference 死常量导出；historyDelete（History.vue 删除后遗留）/historyClearByType/getCustomModel 死导出；$refs.upload 死引用 | 全部删除 |
| P3 | wasCancelled 分支谎称"未生成结果"而 standalone 已入库 | 文案并入 standalone 事实 |
| P3 | 单文件文件夹选择不再强制关裁剪（相对旧版行为漂移） | 接受为有意简化，注释更正 |

### 遗留观察项（不阻塞，记录备查）

- 上传端点不校验 tif 魔数（.tif 命名的任意字节可入 static/upload），坏文件在推理端以 500"后端出现未处理异常"终结（消息已脱敏无路径泄露）——建议后续把 rasterio 读取错误映射为 400 业务错误。
- walkFileTree 对超大文件夹无条目上限、子树读取错误静默丢弃（共享化后修它的成本已降低）。
- 项目模式"一键清空历史"只清 flash 目录（旧有限制）。
- loading.js 计数器在 silent/非 silent 混合并发时可能提前关他人遮罩（旧代码同构，有防负守卫）。
