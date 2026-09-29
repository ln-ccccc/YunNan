# GUI 全面测试 + 代码结构评估报告（2026-09-27）

按「先全面记录问题 → 制定计划 → 批量修复」的巡检方式执行。本文为问题清单与修复计划，**本轮未修改任何代码**。

- 测试对象：Miner 工作台 http://localhost:4000（三容器栈，全 8 容器 healthy）
- 方法：浏览器黑盒 GUI 测试（真实点击/输入），高频截屏存证于 `docs/images/gui-audit-20260927/`，DOM 快照与 API/DB 交叉验证根因
- 账号：admin（.env）。未做破坏性操作：未删真实项目、未执行快照恢复、未触发真实 GPU 推理
- 工具限制：IAB 工具链无 console 监听能力，本轮以页面可见表现 + API/DB 对照为证据；文件上传（IAB 不支持 filechooser）未测

---

## 一、测试覆盖结论

| 区域 | 结果 | 关键证据截图 |
| --- | --- | --- |
| 登录/登出/错误密码 | ✅ 全通过（错误提示红色醒目、无布局跳动） | t1_login_initial / t1_login_wrongpw |
| 项目管理（3 项目、八面板、筛选、分页 1/29） | ✅ 功能通过；3 个问题见清单 | t2_projects_full / t2_projects_yunnan |
| 新建项目表单（打开/空表单校验/取消） | ⚠️ 空表单保存无文案反馈（#4） | t2_form_empty_save |
| 地图视图（瓦片/聚合/详情弹窗/7tab/时序播放/NDVI） | ⚠️ 功能可用但标记点击命中率极低（#6） | t3_map_view / t3_mine696_modal / t3_play |
| 影像管理（选项目→31 条清单加载） | ✅ 通过；不继承项目上下文（#7） | t4_imagery / t4_imagery_loaded |
| 智能解译 / 图斑编辑 | ✅ 页面正常（空态文案规范）；同 #7 | t5_interpretation / t5_editing |
| 数据管理（导出 4 格式、快照、日期补零格式） | ✅ 通过；同 #7、时间早 8h 见 #1 | t6_data / t6_data_loaded |
| 查询搜索（矿山 696 跨项目定位） | ✅ 通过 | t6_search_696 |
| 系统设置（改密错误路径「原口令不正确」） | ✅ 通过 | t6_settings_pwderror |

瓦片服务实测 3-5ms/片（z8 4 片 → z15 24086 片金字塔完整）；NDVI 项目级聚合（均值 0.333/趋势 +0.0746/覆盖 565/565）与单矿指标（696：均值 0.219/MK upward）均正确上屏。

---

## 二、问题清单（按严重级排序）

### P1（数据正确性 / 核心交互受损）

**#1 快照与导出记录时间显示早 8 小时（时区序列化不一致）**
- 现象：同一事件（09-25 生成快照 #2），「项目活动/档案最近活动」显示 22:53:19（正确，北京时间），「导出与快照 → 项目配置快照」与数据管理页导出记录显示 14:53:19 / 15:10:31（错误，实为 UTC 原文）。
- 根因（已定位）：MySQL/后端容器均为 UTC；`/api/projects/:id/timeline` 走 `service.py:1337` 后补 Z（`_utc_timestamp` 口径，前端正确换算 +8），而 `/backups`、导出记录走 `ProjectBackupRecordSchema.dump()`（`service.py:1462-1467`）输出**无时区标记的 naive UTC 串**，前端 `formatDateTime` 按本地时间解析 → 慢 8 小时。
- 修复：所有时间 DTO 统一走 `_utc_timestamp`（schema dump 后处理或 schema 字段 level 补 Z）；补「同记录两接口时间一致」回归 fixture。

**#2 「监测面积 0.00 公顷」——面积单位链路断裂**
- 现象：地图页数据概览显示监测面积 0.00 公顷；单矿 696 详情却显示 9375.71 m²。
- 根因（已定位）：`project_mine_binding.area_snapshot` 以**万公顷量级**存储（565 座合计 0.000697 万公顷 ≈ 7 公顷 ≈ 70,000 m²，DB 实查吻合）；`project_map.py:186` 直接求和返回 `mineAreaTotal`，前端以「公顷」标签直接展示该值 → 0.00。geojson 的 `TBTYMJ`（m²）与绑定快照（万公顷）双口径并存。
- 修复：统一面积 DTO 单位（建议 m² 或公顷二选一），后端聚合时换算；前端标签与换算对齐；单矿/项目两级口径一致性测试。

**#3 地图矿山标记点击目标过小，详情弹窗实际不可经地图直点打开**
- 现象：省级初始视野下矿山多边形实测仅 ~2px（DOM 实测 43 个 path 宽 2px），多次点击均未命中；仅当搜索定位 flyTo 到 300m 级（多边形放大）后才可点击。列表/搜索路径正常。
- 影响：核心交互「点矿山看详情」在默认视野下可用性趋近于零。
- 修复：为矿山层增加命中容差（Leaflet canvas renderer `tolerance`，或叠加透明命中 circleMarker），或小缩放级别以点符号渲染、放大后再出多边形。

### P2（交互体验明显缺陷）

**#4 新建项目空表单保存无任何校验反馈**：点击「保存项目」仅聚焦「项目名称」输入框，无红色文案、无 toast（对比登录页有「账号或密码错误」）。位置：`ProjectForm` 提交路径。修复：补「请填写项目名称」等行内校验文案。

**#5 模块页不继承项目上下文（4 处）**：影像管理/智能解译/图斑编辑/数据管理进入后均为「请选择项目/先选择项目」，且「项目工作台（上传/KML）/刷新清单/发起新解译」禁用无解释。用户在七模块间切换需反复选项目。修复：全局项目上下文（composable + localStorage/URL 同步），模块页自动选中工作台当前项目；禁用按钮加 title 提示。

**#6 解译进度日期格式残留**：项目卡片「已完成（CPU 回退）（2026/9/25）」用斜杠不补零格式，与全站补零 ISO（2026-09-25 15:10:31）不一致。根因：`ProjectSelector.vue:111` `toLocaleDateString('zh-CN')`，未走 `utils/formatDate.js`（上轮格式统一批遗漏处）。

### P3（一致性 / 打磨）

**#7 项目下拉选项英文状态后缀**：「云南矿山生态修复监测项目（active）」——active/draft 英文与全站中文状态词（进行中/草稿）不一致。各模块页项目选择器共用此格式。
**#8 右侧详情面板八段垂直堆叠无锚点**：565 矿山大项目下信息层次被稀释，需大量滚动（视觉评估确认）。建议分组 tab 或粘性目录。
**#9 登录页细节**：说明文字孤行（「地图。」单独换行）、输入框无 placeholder、无错误预留区（首行占位）。

### 观察项（不构成缺陷，供产品判断）

- O1 地图底图为离线区域金字塔（z8-15），初始视野含范围外灰区，无「底图覆盖边界」提示；矿山矢量整体在底图 bounds 内（50225 顶点 0 越界）。
- O2 变化矩阵数据缺失（valid_mine_count=0/565），前端空态文案规范（「该矿山暂无变化矩阵数据。」）——数据管线待补，前端处理正确。
- O3 矿山标记/左栏矿山列表无可访问角色（a11y 树不可见），键盘/读屏不可达。
- O4 运行态与 git 不同步：`miner/routes`、`miner/services` 活挂载进 miner-api 容器，**未提交的 retained-basemaps BFF 改动已在运行态生效**，需尽快提交防丢失（HEAD 中无此代码）。
- O5 正面确认：NDVI 真实数据上屏、时序播放（2017→2025）、聚合展开、搜索跨项目定位、改密错误路径、空态文案、数据管理页日期补零、瓦片性能均达标。

---

## 三、修复计划（建议批次）

**批次 A（数据正确性，先行）**：#1 时间序列化统一（含回归 fixture）→ #2 面积单位链路（含 DB 存量确认）→ #6 日期格式残留（顺手）。验证：后端全量 pytest + miner node --test + GUI 复验两处显示。
**批次 B（核心交互）**：#3 矿山命中层（canvas tolerance 或命中符号）+ #4 表单校验文案。验证：Playwright/GUI 点击循环命中率统计 + 空表单截图。
**批次 C（体验一致性）**：#5 项目上下文继承（涉及 4 模块页，建议 composable 统一）→ #7 状态中文映射 → #8 面板锚点 → #9 登录页细节。
**随批次提交**：O4 的未提交 BFF 改动先行入库。

---

## 四、代码结构评估（只读调查结论）

总体：契约落地程度高（readiness 单一归属后端、BFF 主体纯转发、技术债标记几乎为零、测试有 fixture 守护）。需要处理的结构问题按影响排序：

| 级别 | 问题 | 证据 | 建议 |
| --- | --- | --- | --- |
| 高 H1 | imagery candidates 公开 DTO 泄漏 `dataset.file_path`（绝对路径），前端回传作 clip/slice source；且 storage_key 候选实际不可用（发 undefined→400），属契约 §6 硬违规+死分支 | `imagery_processing.py:94`、`ImageryView.vue:270-272,316-318`、`test_imagery_processing.py:116` | candidates 统一 `storage_key`/`dataset_id`；`_resolve_source` 收紧；同步 fixture；先核存量数据迁移 |
| 高 H2 | ProjectArchivePanel 自请求 /assets /backups /timeline，与父级 slices 重复（§7 违规，选中项目双份流量） | `ProjectArchivePanel.vue:120-144` vs `ProjectWorkspace.vue:349-367` | 改 props 传入；合并两份事件中文映射 |
| 高 H3 | `project_hub/service.py` 1747 行 god module：项目域+导出制品写出+快照恢复+overview 序列化同文件（导出属 Results 职责） | `service.py:883-1302,1341-1745` | 按 exports/snapshot/overview/project 拆文件，契约不变分 PR 搬家 |
| 中 M1 | `ProjectWorkspace.vue` 881 行协调层过宽；639-675 两处裸 axios 漏拼 API base | `ProjectWorkspace.vue:643-645,659-661` | 端点并入 projectWorkspaceApi；空间向导抽 useSpatialWizard |
| 中 M2 | `spatial_status/missing_resources` 双轨词汇（A 轨 spatial_state.py vs B 轨 readiness 重算覆盖同名键），前端向导绑 A 轨 | `spatial_state.py:4-20` vs `service.py:221-253` | overview 统一由 readiness 派生，走契约→提供方→消费方顺序 |
| 中 M3 | BFF 塑形违规：地类聚合在 BFF（`routes/projects.js:132-149`）、KML 历史在 BFF 排序（与自身头注释矛盾） | `routes/interpretation.js:35-47` | 下沉 Flask 或明确豁免；排序移前端 |
| 中 M4 | 前端复制模式：loadProjects 6 份、EDITABLE_STATUS 2 份、生命周期中文映射 3 份、/stats/overview 2 处请求；MapDashboard 与 useProjectInference 重复加载影像 | 各视图 63-88 行等 | useProjectList/常量模块/useMineData 透出 loadImageryAssets |
| 中 M5 | 测试缺口：ProjectWorkspace 及全部面板零行为测试；server.js 瓦片守卫、authBackend、全部 composables 无测试；MapDashboard 仅源码正则守护 | miner/test 布局 | 拆分前先补行为测试；优先 server.js 守卫与 useMineData |
| 低 L1-L7 | ModulePlaceholder 死代码；SearchView N+1 geojson 扇出；routes 451 行样板 relay 收编；列表 DTO 补 lifecycle_status 别名；BFF 直读文件系统长期收敛 Flask；miner/ 根运行产物归置；ImageryView 硬编码 RASTER_BBOX | 详见调查 | 按需排期 |

结构优化推进原则（与修复计划并行不冲突）：先 H1（唯一契约硬违规，顺带消除死分支）→ H2（小而确定）→ M4/M1（为 #5 项目上下文改造铺路）→ H3/M2（大搬家，分 PR）。

---

## 五、交互体验建议（T10）

1. **项目上下文是一等公民**：当前「项目工作台选了项目，其他模块全忘」是最大体验断裂（#5）。建议顶栏常驻当前项目 chip（可切换），七模块共享。
2. **地图直点矿山不可用（#3）是核心链路**：巡检用户的高频动作就是「点矿山→看趋势」，建议命中层修复后补充 hover 放大 + 点击后自动 flyTo（现已部分具备）。
3. **反馈一致性**：登录错误有文案、项目表单没有（#4）；建议统一「行内红色文案+聚焦」模式，禁用按钮统一 title 说明原因。
4. **长面板导航（#8）**：详情面板可折叠分组或右侧 mini 目录；「登记推理影像」表单对非管理员高频使用，可考虑与「配置空间资源」合并入口。
5. **空态引导**：各模块「先选择项目」空态可加一步引导按钮（「去项目工作台选择」），而不是等用户自己发现。
6. **命名一致性**：同屏「解译平台/智能解译/开始地物分类/推理」多种叫法并存，建议统一词表（解译=推理 inference；编辑=修订 revision）。

## 六、视觉重心评估（T10）

- **整体**：墨绿主题贯穿（登录→侧栏→按钮），专业感强、一致性高；三栏工作台结构清晰。视觉重心落在**顶部统计带**（项目总数/矿山总数/图斑/异常）——数字大、色块重，用户第一眼看数据概览，符合监测平台定位。
- **项目管理页**：左列表右详情双栏比重均衡，但右侧八段纵向流导致「重心下沉」——首屏是概览卡，快照/档案/图斑清单全在折叠线下方；建议分组后首屏露出「工作流检查+下一步」即止。
- **地图页**：重心在地图（正确），左「数据概览」右「分析统计」对称配重；NDVI 卡数值+副标题设计可读性好。聚合数字标记视觉突出，是天然的视觉锚点。
- **弱项**：登录页说明文字孤行（#9）；项目卡片内状态徽章与统计行密度高、行距紧；弹窗头部信息条（ID/面积/状态/坐标）信息密度大但层级平，可给「面积/状态」做视觉分组。
- **建议保留**：深绿主色、状态色系统（绿=已治理/红=未治理）、ECharts 配色已是全站统一语言，无需大改。
