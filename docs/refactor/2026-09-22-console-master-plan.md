# 矿山生态修复监测平台 主控台化改造：需求确立与执行计划 2026-09-22

## 0. 需求来源与决策记录

用户原始诉求四块：①首页可视化主控台（左侧导航+项目卡片+图斑下钻）；②独立项目管理（增删改归档/导入导出/批量/一项目一档案）；③查询检索（项目模糊查询+图斑溯源查询）；④辅助体验（统计看板+地图工具）。

**已确认的四项边界决策**（2026-09-22 与负责人对齐）：
1. 主控台建在 **Miner**（GeoView 保持解译工具定位，导航项带项目上下文跳转）；
2. 分类结果**仅推理产生+人工修订**（GeoView 编辑器现状即满足"可编辑"，不做人工从零新建）；
3. 项目批量操作=**批量归档/删除+快照导入导出**（不做 Excel 批量新建——AGENTS §9"通用项目管理平台"边缘）；
4. 图斑溯源=**聚合溯源面板**（不做原始影像在线叠加浏览——需影像瓦片服务，另立专项）。

## 1. 现状 vs 需求 Gap 矩阵（两路独立盘点汇总）

| 需求条目 | 现状 | 缺口 | 处置 |
| --- | --- | --- | --- |
| 左侧固定导航栏（七模块） | 无路由系统，仅 `#/projects`+`#/map/:id` 双视图（App.vue:10-27, viewNavigation.js:1-22） | 整个主导航层 | M1 新建 |
| 首页项目卡片（名称/区域/年份/**解译进度/图斑数量**） | 卡片有名称/区域/监测期/矿山数/数据数（ProjectSelector.vue:44-60） | 缺解译进度、图斑数量 | M1 增强后端 list + 卡片 |
| 点选项目显示图斑明细 | 地图点选弹窗（NDVI/矩阵/分类/原始影像）已有；无图斑列表页 | 图斑列表下钻视图 | M3 |
| 项目新建/编辑/归档 | **已具备**（ProjectForm + POST/PATCH + archive/restore） | — | 无 |
| 项目删除 | **完全缺失**（无 DELETE 路由；Project 模型有 deleted_at 软删机制） | 删除端点+安全规则 | M2 |
| 基础信息导入导出 | 导出=成果数据；配置快照已有（backups list/create/restore） | 快照文件级导入（跨环境迁移） | M2 |
| 批量归档/删除 | 零 | 批量端点+UI | M2 |
| 一项目一档案 | 目录隔离+8 类资产台账已有 | 档案视图整合（轻） | M2/M4 |
| 项目模糊查询 | 名称/区域/年份/状态筛选已有 | 全局搜索入口整合（轻） | M4 |
| 图斑溯源查询 | MineDetailModal 仅两期对比；无聚合端点 | **核心缺口**：按 fid 聚合历年成果/修订/变化/占比 + 溯源面板 | M3 |
| 地类占比（看板） | 单项目 landTypeList（计数非占比） | 跨项目占比聚合（按矢量面积） | M1 |
| 新增修复面积 | changeAreaStats 硬编码 0 | 从变化矩阵净转移×像素面积计算 | M1（含指标定义） |
| 疑似异常图斑数 | 概念不存在 | 需定义判定规则（计划 §4.3 给默认规则） | M1（含指标定义） |
| 地图工具 | 仅缩放+图例 | 图层显隐 UI/比例尺/网格辅助 | M4 |
| 图斑编辑入口（Miner 侧） | 仅顶栏整页跳 GeoView | 导航项+成果选择跳转 | M3 |

**顺带发现的数据链缺陷**（M3 一并修）：`class_ratio_percent.json` 与 `change_matrix_*.csv` 不满足 `fid+` 前缀规则、被文件服务路由挡在门外（api/project.py:121-145）；trend_reports 目录无任何写入方（空数据端点）。溯源 API 将从后端内部直读这些文件，不走静态路由。

## 2. 总体架构

- **导航骨架**：Miner 引入轻量 hash 路由表（扩展现有 viewNavigation.js，不引 vue-router——保持依赖最小），左侧固定导航栏 `AppShell`（七项：项目管理/影像管理/智能解译/图斑编辑/数据管理/查询搜索/系统设置），顶栏保留。
- **模块落点**：
  - 项目管理 `#/projects`（现首页改造：统计看板+项目卡片+筛选）
  - 项目详情 `#/map/:id`（现地图工作台不变）
  - 图斑明细 `#/map/:id` 工作台内新"图斑清单"面板（矿山列表+状态+图斑数+溯源入口）
  - 影像管理/智能解译/图斑编辑/数据管理：整合现有面板为新路由页（内容复用 ProjectSpatialResources/InferenceModal/编辑器跳转/Assets+Export）
  - 查询搜索 `#/search`（项目条件查询+矿山 ID 跨项目定位）
  - 系统设置 `#/settings`（首期仅基础信息展示+口令修改入口占位，不做 RBAC）
- **GeoView 不动**，仅作为图斑编辑器与解译工具被跳转（沿用 geoviewNavigation.js 模式）。

## 3. 分期执行计划（M1→M4，每期独立交付可验证）

### M1 主控台骨架 + 首页看板（结构期，最大）

**契约先行**（新增 API，先 fixture 后实现）：
- `GET /api/stats/overview`：跨项目聚合——项目数（在建/已完成/已归档）、矿山总数、图斑要素总数、地类占比（按各项目 classification_results 的 current FC 类别×等积面积聚合）、新增修复面积（Σ 各矿山变化矩阵净修复像素×像素面积，像素面积从 label.tif transform 读取）、疑似异常矿山数（见 §4.3）。
- `GET /api/projects` list 增强：每项 + `latest_inference`（最近 job 状态/时间，子查询）+ `feature_count`（该项目全部成果 current FC 要素数；**实现取冗余列**：ClassificationResult 加 `feature_count` 整型列，发布/保存修订时更新，一次性迁移回填）。

**前端**：AppShell 左导航 + 路由表扩展（viewNavigation 重构+测试）；首页=统计看板卡（echarts 环形/趋势）+项目卡片网格（新增两字段）+筛选条保留。
**验证门**：后端 pytest（stats 聚合含空项目/无成果项目 fixture）+ miner node --test（路由表/看板纯函数）+ build + GUI 实测；ocr delegate 规则审查新增文件。

### M2 项目管理增强

- `DELETE /api/projects/:id`：**仅 archived 项目可删**；删除=deleted_at 软删（审计轨迹保留）+ project_storage 目录整目录**归档移入** `project_storage/trash/<id>_<date>/`（不物理清除，可人工恢复）；活动项目必须先归档。二次确认 UI 文案明示后果。
- `POST /api/projects/batch/archive` `/batch/delete`：{project_ids[]}，逐项复用单项目服务，单事务，返回逐项结果（部分失败不回滚全部，逐项报错）。
- 快照导入：`POST /api/projects/:id/backups/upload`（multipart，复用快照格式校验+恢复流程）——跨环境"一项目一档案"迁移的导入侧。
- 档案视图：项目工作台新增"项目档案"面板（汇总 8 类资产+存储占用+最近活动时间线摘要——数据全部已有，纯整合）。
**验证门**：删除安全规则测试（活动项目 409/归档可删/目录归档验证）、批量部分失败 fixture、BFF 路由测试、GUI。

### M3 图斑溯源（核心业务价值期）

- `GET /api/projects/:id/mines/:fid/traceability`（只读聚合，一次返回）：
  - `years[]`：按年条目——成果图/原图 png URL、result_id、vector_status、feature_count、类别占比（直读 class_ratio_percent.json）、原始影像引用（复用 existing original-imagery 端点数据）
  - `revisions[]`：修订时间线（audit 表：revision_no/author/action/feature_count/时间）
  - `change`:最近两期矩阵 + 多年占比序列（class_ratio series_percent 直读）
  - `indices[]`：NDVI 等时序（直读 indices/<fid>.json）
  - 去重规则：同年多 job 取最新成功 job 的成果（发布时间序）。
- Miner 图斑清单面板：项目工作台新面板（矿山 fid/名称/状态/图斑数/最新年份/溯源按钮）+ MineDetailModal 升级为**溯源面板**（历年成果图并排滚动对比+占比堆叠时序图+修订时间线，替代现"地物分类"仅两期视图）。
- 图斑编辑导航项：`#/editing` → 选项目 → 成果列表（year/vector_status/要素数）→ "编辑"跳 GeoView 编辑器（带 project_id+result_id）。
**验证门**：聚合 API 测试（多年/单年/无数据/同年多 job 去重）、BFF 中继、溯源面板纯函数测试、GUI 实测项目 1 矿山 713（2024 单年起步+多年后扩展）。

### M4 体验完善

- 地图工具：L.control.layers（三套底图+矿山图层显隐）、L.control.scale（比例尺）、经纬网辅助（轻量 graticule 实现，开关默认关）。
- 影像管理页：整合空间资源向导+影像登记+KML 上传入口（BFF /api/kml/upload 已有无前端调用方——补 UI）。
- 数据管理页：资产+导出+快照整合（复用现有面板组件化）。
- 全局搜索页：项目条件查询（复用 list 参数）+ 矿山 ID 跨项目定位（遍历项目 mines 匹配 fid → 跳转）。
- 系统设置页：基础信息（版本/存储占用/运行状态）+ 修改口令入口。
**验证门**：地图工具 GUI 实测、导航死链测试（deadLinkGuards 扩展）、全量回归。

## 4. 需要负责人知悉的默认规则（可在评审时改）

1. **删除语义**：软删 DB+目录移入 trash（不物理清除）；"彻底清除"不在本期。
2. **图斑数量口径**：classification_results 的 current FC 要素数（人工修订后即时变化），非"矿山数"。
3. **疑似异常矿山默认规则**：最新两期变化矩阵中（草地+林地）净流向（裸地+建筑）的像素占比 ≥10% 的矿山计为疑似异常；阈值与类别的映射常量化可调。
4. **新增修复面积口径**：（草地+林地）净增加像素 × 像元面积（米²，从 label.tif 的 transform 推算），跨项目求和。
5. **同年多成果**：溯源与图斑数量均取该年最新成功 job 的成果。

## 5. 执行纪律（沿用本会话已验证的方法论）

- 每期一个主 PR，契约→提供方→消费方→集成验证顺序；跨模块 fixture 先行。
- 每期完成跑三套门：模块测试（容器 pytest / node --test）+ 构建 + GUI 实测；新增文件过 **ocr delegate 规则审查**，整期 diff 过 **agent-skills 五轴**独立审查（写码/审查分离），P0/P1 当期修。
- 指标口径（§4.3/4.4）实现前先与负责人确认数值样例对齐（用项目 1 矿山 713 实测数据人工核对）。
- 估计量级：M1 ≈ 后端 2 端点+迁移+导航骨架（最大）；M2 ≈ 3 端点+UI；M3 ≈ 1 聚合端点+两面板（业务价值最大）；M4 ≈ 纯前端整合。

## 6. 明确不做（本期边界重申）

人工从零新建分类结果；原始影像在线瓦片叠加浏览；Excel 批量新建项目；RBAC/多用户权限；要素级修订 diff 审计（记为 v2 候选：ClassificationEditAudit.details_json 扩展）；修订回滚（vector-result-v1 契约维持）。

## 7. 执行结果（M1-M4 全部交付，2026-09-22 当日）

| 期 | 提交 | 核心交付 | 验证 |
| --- | --- | --- | --- |
| M1 主控台骨架+看板 | f84bd36 | AppShell 七模块左导航+占位、StatsOverviewPanel 五卡+地类占比、feature_count 冗余列、项目卡片两新字段 | backend 404/0、miner 61/61、GUI |
| M2 项目管理增强 | 363fe18 | 删除（仅归档可删+目录入 trash）、批量归档/删除、快照导入、项目档案面板 | backend 613/0、miner 64/64、GUI |
| M3 图斑溯源 | 80ea1d0 | traceability 聚合端点、成果清单端点、详情溯源视图、#/editing 编辑导航 | backend 717/0、miner 64/64、GUI（矿山 713） |
| M4 体验完善 | 739ebac | 图层显隐/比例尺/经纬网、影像管理页、搜索页（矿山 ID 跨项目定位）、设置页 | backend 717/0、miner 64/64、GUI |

最终基线：backend 717 passed / 0 failed、miner 64/64、frontend 44/44、双构建零错误。
导航施标：七模块中五模块独立页面（projects/imagery/editing/search/settings），智能解译与数据管理标注"项目工作台内操作"（发起推理与导出/快照本就在工作台流程内）。
