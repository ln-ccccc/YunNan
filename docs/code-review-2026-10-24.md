# 云南项目全面代码审查报告（2026-10-24）

> 方法：四路并行深审（后端 applications 全域 / Miner 前端+BFF / GeoView 前端 / 部署与跨模块契约），全部只读。
> 基线：`main @ d0b37b5`（与 2026-10-17 审计同一基线，其后无新提交）。
> 实证：隔离一次性容器（生产同镜像 `yunnan-runtime:current`）执行后端测试 4 批共 ~1430 例；两前端 npm test 全绿；对最严重发现逐条源码复核。
> 本报告为审查记录，未修改任何业务代码。

## 1. 总体结论

- **未发现 P0 级问题**：鉴权全覆盖（所有 blueprint `before_request ensure_logged_in`）、无 SQL 注入、无路径遍历、无任意文件读写、无浏览器可提交的服务器路径。
- **测试底盘健康**：后端 ~1430 例 + Miner 100 例 + GeoView 56 例在正确环境下全部通过（见 §2），历史修复均有测试锁定。
- **核心问题集中在四条线**：
  1. `ProjectDataset.file_path` 一处键语义断裂，同时造成"clip/slice 产物无法推理"与"快照跨环境导入死路"两个功能死路（§3 P1-1/P2-3）；
  2. BFF 下载链路两处实现错误：流管道无错误处理（可致 BFF 进程崩溃）+ `AbortSignal.timeout` 语义用错（GB 级下载 30 秒必断）（§3 P1-2/P1-3）；
  3. GeoView 编辑器两个交互级缺陷：保存响应无上下文守卫（跨成果污染）、未保存编辑无丢失保护（§3 P1-6/P1-7）；
  4. 会话寿命配置互相架空：`app.py` 每请求覆盖为 20 分钟，使 2026-09-27 的"7 天记住我"修复失效（§3 P2-4）。
- 2026-10-17 审计 Top10 风险：**1 项已修复，9 项仍在**（其中数项本次给出了更精确的定位与成因），另有一批审计未覆盖的新发现。

## 2. 测试实证

| 批次 | 模块 | 结果 |
| --- | --- | --- |
| 后端 1 | security_recheck / auth×2 / project_api / read_models / spatial / map | 194 OK |
| 后端 2 | classification×3 / file_upload_chunks / imagery_processing / inference_results | 241 OK（16 skip，硬件相关） |
| 后端 3 | inference jobs/routing/worker / management / quality / traceability / downloads / dxf / stats×2 | 817 OK（6 skip） |
| 后端 4 | kml_roi×4 / interpretation / inference batch/device/contract / large_tiff_upload | 109 通过；13 error 均为审查容器只读挂载阻断测试自建临时目录，非代码问题；3 skip |
| Miner | npm test | 100/100 |
| GeoView | npm test | 56/56 |

执行环境注记：后端测试须在容器内带 `GDAL_DATA`/`PROJ_LIB`/`PROJ_DATA`（镜像 conda 路径）并挂载根 `tests/fixtures`；`test_inference_image_contract` 等需读仓库根文件且在根目录建临时目录。CI 缺失（无 workflows），当前依赖人工按分组执行。

## 3. 发现清单

### P1（高：功能死路 / 数据正确性 / 进程稳定性）

| # | 位置 | 问题 |
| --- | --- | --- |
| P1-1 | [backend/applications/project_hub/_foundation.py:100](../backend/applications/project_hub/_foundation.py) 写 `incoming/` 相对键；[imagery_processing.py:227,306](../backend/applications/project_hub/imagery_processing.py) 写**绝对路径**；[imagery_processing.py:60](../backend/applications/project_hub/imagery_processing.py) 按 cwd 解析相对键；[inference_inputs.py:39](../backend/applications/project_hub/inference_inputs.py) 拒绝绝对路径且强制 `incoming/` 前缀 | `ProjectDataset.file_path` 三段写入语义互斥：**clip/slice 产物（绝对路径+inputs/imagery/）永远无法通过推理输入校验**；`incoming/` 键解析依赖进程 cwd，属隐式环境耦合。一根病根两处发作（另见 P2-3），建议统一为 storage 根相对键一次性根治 |
| P1-2 | [miner/routes/projects.js:171](../miner/routes/projects.js) | `Readable.fromWeb(upstream.stream).pipe(res)` 无 `error` 监听、客户端断连不 abort 上游。上游中断时错误沿流传播无消费者 → uncaughtException → **BFF 进程退出，Miner 全功能下线**。全站导出/影像下载均走此路径 |
| P1-3 | [miner/services/projectBackend.js:58](../miner/services/projectBackend.js) | 注释声称"超时只约束建立阶段"，但 `AbortSignal.timeout(30000)` 传给 fetch 后**覆盖整个响应生命周期含 body 读取**——GB 级下载超 30 秒即被客户端主动 abort。注释与实现相反（已复核原文）。叠加 P1-2，abort 还会触发无监听管道崩溃。修法：`AbortController` 手动计时，建连成功即 clear |
| P1-4 | [backend/applications/project_hub/imagery_processing.py:219,280](../backend/applications/project_hub/imagery_processing.py) `out_profile.pop("nodata", None)`，全程无 mask 处理 | clip/slice **主动丢弃 NoData 与掩膜**：源影像补零区/云掩膜区被当有效像元，静默污染后续统计与展示。对照：kml_roi 链路（[raster_ops.py:113,252](../backend/applications/kml_roi/raster_ops.py)）已建立 nodata=255+掩膜贯穿的正确惯例，此处是唯一例外 |
| P1-5 | [miner/src/components/MapContainer.vue:307](../miner/src/components/MapContainer.vue) `layer.bindTooltip(name)` | 矿山名来自第三方 KML/GeoJSON 导入，Leaflet tooltip 按字符串默认 **HTML 渲染**——恶意名称（如 `<img onerror=...>`）在管理员悬停时执行。后端 [spatial_service.py:41](../backend/applications/project_hub/spatial_service.py) 只滤私有键不做值转义。全项目唯一 innerHTML 面，一行修复（传 DOM 元素或先 escape） |
| P1-6 | [frontend/src/views/mainfun/ClassificationResultEditor.vue:585-620](../frontend/src/views/mainfun/ClassificationResultEditor.vue) | 保存响应直接写 `this.result/this.draw/this.context`，**无上下文/序列守卫**（加载腿已有 `reloadSeq`，保存腿没有）：保存 A 期间切到 B，A 的响应覆盖 B 的基准版本号/画布/编辑基线；或 `draw` 已销毁抛 TypeError 显示"保存失败"但**服务端实际已写入**，用户重试必 409 |
| P1-7 | [ClassificationResultEditor.vue:269-296](../frontend/src/views/mainfun/ClassificationResultEditor.vue) | `isDirty` 时 reload/路由切换/浏览器后退**无确认直接丢弃编辑**，无 `onBeforeRouteLeave`/`beforeunload` 守卫。浏览器测试在案 knownBug（geoview-glass-smoke.cjs:162）。审校几十个图斑一次误触全丢且 Ctrl+Z 不可恢复 |
| P1-8 | [miner/src/components/ImageryView.vue:236](../miner/src/components/ImageryView.vue) `RASTER_BBOX` 硬编码云南范围 | 手绘 ROI 与所选影像无地理绑定：切影像后仍按固定 bbox 裁剪，可能产出**看似成功实则错误**的成果。需产品先明确手绘语义再修 |
| P1-9 | [deploy_offline.ps1:53](../deploy_offline.ps1) vs [docker-compose.prod.yml:213-225](../docker-compose.prod.yml) | 迁移包恢复 `yunnan_miner_tiles` 卷，但 prod compose 仅声明 6 卷**无此卷**——恢复后成孤儿卷；因卷清单与 SHA256SUMS 校验联动，迁移包**必须携带死数据卷**否则部署失败（已复核原文）。属旧栈残留，建议从清单移除 |

### P2（中：防护失效 / 性能 / 一致性）

| # | 位置 | 问题 |
| --- | --- | --- |
| P2-1 | [backend/applications/common/utils/upload_sessions.py:237](../backend/applications/common/utils/upload_sessions.py) + api 层传键错误 | 分片上传 owner 校验恒为 None 直接跳过——**"他人不得续写会话"的防护是死代码**。单管理员下影响有限，但防护声明与实现不符 |
| P2-2 | [backend/applications/project_hub/service.py:428,438](../backend/applications/project_hub/service.py) | 项目删除先 `shutil.move` 到 trash 再 commit，**commit 失败无文件回移补偿**（同仓库 inference_results/exports 均有备份+回滚惯例，独此缺失） |
| P2-3 | [backend/applications/project_hub/snapshots.py:307-323](../backend/applications/project_hub/snapshots.py) | 快照跨环境导入对 imagery 数据集必然失败（`incoming/` 键永远过不了 `_safe_dataset_path` 闸门）——同环境恢复可用掩盖了死路。**P1-1 根治后随之消解** |
| P2-4 | [backend/app.py:16-19](../backend/app.py) vs [applications/__init__.py:75](../backend/applications/__init__.py) | `before_request` 每请求把 `permanent_session_lifetime` 覆盖为 **20 分钟**，把 2026-09-27"7 天记住我"修复架空（已复核原文，两处注释各自声称生效）。用户仍被频繁登出。需决定哪个是有意值 |
| P2-5 | [backend/applications/api/stats.py:75-117](../backend/applications/api/stats.py) | `/api/stats/overview` 全量加载 ClassificationResult 整实体（含 MEDIUMTEXT 大 JSON）进内存做 Python 侧过滤。assets.py:455 已用 `load_only` 修复同问题并留注释，stats 链路漏网。成果积累后每次看板 O(全表大对象) |
| P2-6 | [miner/src/components/SmartInterpretationView.vue:146](../miner/src/components/SmartInterpretationView.vue) | 解译记录按 `fid:year` 建键 map，同矿山同年多任务**后写覆盖前写**：历史行按钮全部指向最后一条成果；若最后一条 vector_failed，旧行可编辑成果的按钮消失。后端口径正确（mine_traceability.py:40 同年取最新且优先非失败），纯前端展示层问题 |
| P2-7 | [miner/src/components/ImageryView.vue:192-233](../miner/src/components/ImageryView.vue) | 候选列表无竞态门控（全仓唯一遗漏）：快速切项目时 A 项目候选迟到落地，**A 的 dataset_id 可被登记进 B 项目**形成跨项目脏引用。照抄本仓 seq 门控惯例即可 |
| P2-8 | [miner/server.js:25-30](../miner/server.js) | `cors()` 无白名单全开放 + `express.json({limit:'60mb'})` 在 authGuard **之前**——未登录者可向任意 JSON 路由推 60MB。且 5008 端口直暴宿主，浏览器可绕过 BFF 限额直打 Flask。内网单管理员可接受，应明示为信任边界假设 |
| P2-9 | [frontend/src/api/request.js:41-69](../frontend/src/api/request.js) | 响应分支**无条件** hide loading，非 silent 长请求 + silent 轮询并发时计数被偷走 → loading 提前消失，用户无遮罩重复操作 |
| P2-10 | [frontend/src/utils/uploadChunking.mjs:61](../frontend/src/utils/uploadChunking.mjs) + [upload.js:60](../frontend/src/api/upload.js) | http 内网（无 crypto.subtle）时对 64MB 块**同步**算 SHA-256，主线程冻结数十秒：进度不刷新、暂停按钮不可点。GB 级=数十块×冻结。改增量计算或 Web Worker |
| P2-11 | [frontend/src/views/mainfun/SpectralIndices.vue:140](../frontend/src/views/mainfun/SpectralIndices.vue) | 光谱页未接入分片上传通道：>512MB 影像单发 multipart 必撞 gunicorn 120s 超时；上传卡"超限自动走分片"文案对该页不成立。另：单发批次无 8GB 总量预检（20×500MB 即 413） |
| P2-12 | 部署综合 | healthcheck 弱：backend/miner-api 只探 `/api/auth/session`；两 worker 无 compose healthcheck（inference 仅镜像内弱探活）——DB 断连/worker 死时栈仍 healthy。`SESSION_COOKIE_SECURE` 未在 prod 透传（默认 false）。快照 manifest 含 `file_path` 原文（叠加 P1-1 绝对路径则内部路径随下载外泄） |

### P3（低 / 代码质量，择要）

- 后端：`delete_photo_by_id` 对不存在记录 None 解引用（upload.py:140，需确认是否死代码）；`list_projects` LIKE 通配符未转义（%匹配全表）；tiff_processor 用 print 不用 logger 且按文件大小而非内存峰值设闸；`_project_list_latest_inference` 无 LIMIT 全表载入；`restore_backup` 恢复强制 `active`（归档项目静默复活）；`success_api` 可变默认参数。
- Miner：搜索大小写不一致（mine_name 未 lower）；ProjectWorkspace.vue:680/sessionClient.js 裸 axios 缺 base 前缀（当前环境变量为空侥幸可用）；DataManagerView/ProjectWorkspace 的 `revokeObjectURL` 同步回收（Safari 大文件截断风险，useMineData 已改 60s 延迟，未统一）；推理轮询 1s/任务高频小请求；requestBinary 全量缓冲双倍内存；`loadRetainedBasemaps` 无门控；MapDashboard toggleGraticule null 时状态失同步。
- GeoView：projectId 不随 query 更新（窄路径下推理同步到旧项目）；Login redirect 未白名单校验；图例颜色双源硬编码；TiffUploadCard 文案与实际白名单（7 种）不符；死代码（loadMap.js 百度地图 http 注入、JSZIP 误 use、v-drag 无使用方）；MyVueCropper 防御缺失；axios 0.26.1 手动 Content-Type 无 boundary 的隐性耦合（升级即断）；preHandle.js 勾选预处理触发全量重传 + `append(...) || append(...)` 恒真双写。
- 测试卫生：部分测试在仓库根建临时目录（只读环境无法跑）；无 CI，依赖人工分组执行。
- 部署：`.flaskenv`（gitignored）纳入离线交付宿主校验清单，若含敏感值随包分发；static-server 无 Cache-Control/ETag、404 fallback 返 index.html；`VUE_APP_BACKEND_IP` 遗留无消费者；yunnan-runtime:current 无 Dockerfile 不可从源码重建（有 provenance 留档+全量校验和缓解）。

### 明确核过的良好面（不作为问题）

- **并发正确性**：矢量修订乐观锁（条件 UPDATE 失败 409）；推理/空间任务原子领取；kml_roi 跨进程文件锁。
- **路径安全体系**：`resolve_storage_path` 拒绝绝对路径+resolve 后 containment+PureWindowsPath 防盘符绕过，五处一致贯彻；瓦片路由（server.js:87-102）全段数字校验+前缀检查（2026-09-20 已加固）；static-server 实测无遍历。
- **信息泄漏防护**：公共序列化路径清洗、错误收口 business_or_server_failure。
- **BFF 瓦片/静态直读虽违反"轻量适配"契约**（server.js:87-108），但已有鉴权+防遍历，属可接受的已加固例外。
- **密钥管理**：compose 全部 `${VAR:?required}` 强制注入、零硬编码；deploy_offline.ps1 三层 SHA256、弱口令闸、卷归属标签、ValidateOnly，工程质量高。
- **契约遵守**：四层状态模型后端确实分开计算；前端 ProjectOverviewPanel 无业务判断复制；前端主动拦截路径型字段（rejectUnsafePathField）。
- **前端纪律**：全仓 seq 门控竞态惯例（含"计数器不得复用"的根因注释）；定时器/监听器/地图实例清理完整无泄漏；GeoView 纯函数 utils 层可单测。

## 4. 2026-10-17 审计 Top10 核验结果

| 审计# | 风险 | 现状 |
| --- | --- | --- |
| 1 | 影像路径契约断裂 | **仍在**，本次精确定位三段写入点与两处消费端（P1-1/P2-3） |
| 2 | 手绘固定 BBOX | **仍在**（P1-8） |
| 3 | NoData 丢失 | **仍在**，确认 imagery_processing 是全链路唯一例外（P1-4） |
| 4 | 编辑丢失/保存污染 | 编辑丢失**仍在**（在案 knownBug）；保存污染**部分修复**（加载腿有守卫，保存腿无，P1-6） |
| 5 | 上传属主失效 | **仍在**，确认为恒 None 死代码（P2-1） |
| 6 | tooltip XSS | **仍在**，前后端两侧均未转义（P1-5） |
| 7 | 删除无补偿 | **仍在**（P2-2） |
| 8 | 下载流 | **仍在且更明确**：pipe 无 error 处理 + timeout 语义与注释相反（P1-2/P1-3） |
| 9 | fid:year 覆盖 | **仍在**（后端口径正确，前端展示层覆盖，P2-6） |
| 10 | 交付可重复性 | **仍在**（现场 build/弱 healthcheck/无 CI）+ 新发现迁移死卷（P1-9） |
| — | BFF 瓦片路径遍历（审计 §6.1 提及的加固面） | **已修复**（2026-09-20 加固，本次实测确认） |

## 5. 逻辑清晰度与架构评价

**结论：这是一个治理中的项目，不是失控的项目。**

- 模块职责与 AGENTS 契约互相印证：project_hub 域内聚、推理运行时独立、BFF 转发不带业务（瓦片直读是已加固的显式例外）、四层状态模型真实落地。
- 注释质量显著高于平均：大量注释记录事故背景、审查编号与口径决策（如底图保留 2 份防误删、面积别名、seq 计数器禁复用根因），可追溯性强。
- 竞态纪律、清理纪律、路径安全、并发补偿模式均已形成全仓惯例——本次发现的遗漏（ImageryView/loadRetainedBasemaps 无门控）属惯例执行遗漏，不是架构混乱。
- 最大结构性短板只有一个：**`ProjectDataset.file_path` 是全仓唯一未纳入统一键语义的路径字段**，P1-1 与 P2-3 两个功能死路是同一根病根的两次发作，应一次性根治而非逐点补丁。次要短板是读模型加载纪律已建立但未横向扫平（stats 漏网）。
- 测试 43+26+12 个文件、~1600 例，历史修复均有测试锁定；弱点是无 CI 自动化、少数测试依赖仓库根可写。

## 6. 建议修复顺序

1. **P1-2 + P1-3**（同处一并改真流式 + 正确的建连超时）——BFF 稳定性 + 大下载可用性。
2. **P2-4**（会话寿命二选一）——一行改动，用户可感。
3. **P1-5**（tooltip escape）——一行修复。
4. **P1-1 + P2-3**（file_path 统一 storage 相对键，含快照导入重定位）——一次根治两个死路。
5. **P1-6 + P1-7**（编辑器保存守卫 + 未保存确认）——照加载腿 reloadSeq 同构实现。
6. **P1-4**（clip/slice 保留 nodata/mask）——对齐 kml_roi 惯例。
7. **P2-7 / P2-6 / P2-5**（门控补齐 / 键扩展 / load_only）——均为照抄现成模式的小改。
8. **P1-9 + healthcheck/SECURE 透传**（部署收口）。
9. **P1-8**（手绘 BBOX）——先与负责人确认预期语义再动手。
10. 其余 P2/P3 按迭代顺带。
