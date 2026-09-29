# 功能完善迭代报告（2026-09-29）

> **续轮补充（同日第二批次）**：①底图格式扩充 IMG/JPG/PNG/ENVI（建议二.1 安全切面）——切片链
> metadata/tiling 本就走 GDAL 通用驱动，仅放宽投递白名单+侧车同行，推理输入固定 GeoTIFF 风险隔离；
> 端到端实测：演练项目投递真实 IMG → 候选识别（GDAL 读 bounds）→ 登记 → 切片 34 瓦片 → 激活 v2
> → `/tiles/` 真实出图，随后用原 TIF 重新登记恢复原状。JPG/PNG 需同名世界文件+.prj，ENVI 需
> .dat+.hdr 成对，无 CRS 文件候选跳过/登记明确报错（e7749d5）。②登录切换瞬态加载失败：浏览器挂
> XHR 4xx 探针实测「退出→登录」完整循环两轮，零 4xx、零错误文案，**未复现**——判定已随 09-27
> 批次的竞态门控/401 拦截收敛，关闭该已知缺口。③双选合并 GUI 实测：生产构建无法访问组件实例
> 驱动 MapboxDraw，且 IAB 无法合成 Shift+点击，维持人工验证项（union 算法与门控已有自动化覆盖）。

按甲方《矿山生态修复遥感解译识别评价软件优化修改建议》（docs/requirements/）逐项核对现状后，
对**在范围内、尚未落地**的缺口做一轮功能迭代。先全面记录 → 制定计划 → 分批实现 → 全量测试 → GUI 复验。

## 一、甲方建议逐项核对结论

| 建议条目 | 状态 | 说明 |
| --- | --- | --- |
| 一.1 主控台/导航/项目卡片 | ✅ 已有 | 项目工作台八锚点 |
| 一.2 项目管理（新建/编辑/删除/归档/批量） | ✅ 已有 | 含批量归档/删除/快照导入 |
| 一.3 项目模糊查询 + 图斑溯源 | ✅ 已有 | SearchView + 矿山详情逐年溯源（影像/识别/掩膜/修订记录/地类占比表） |
| 一.4 统计看板 | ✅ 已有 | StatsOverviewPanel |
| 二.1 影像格式扩充（IMG/ENVI/JPG/PNG） | ✅ 底图面已扩（续轮） | 底图链 GDAL 通用；推理输入固定 TIF（模型管线约束），见上方续轮说明 |
| 二.2 分批导入/断点续传/进度 | ✅ 已有 | 2026-09-22 分片续传 |
| 二.3 裁剪/切片/外扩 | ✅ 已有 | clip/slice + buffer_meters 0~5000m |
| 三.1/3.2/六 模型迭代/样本库 | ⏸ 本期不做 | AGENTS §9 明确排除 |
| 三.3 精度复核校验模块 | ✅ **本轮新增** | 结构性质检+疑似线索（见下） |
| 三.4 预留算法升级接口 | ⏸ 延后 | 固定模型任务架构，模型热插拔需单独设计 |
| 四.1 SHP/DXF/GeoJSON 导出 | ✅ **本轮补 DXF** | 原 geojson/csv/shp/xlsx 已有 |
| 四.2/四.4 图斑编辑/修改溯源 | ✅ 已有 | MapboxDraw 编辑+修订版本+审计 |
| 四.3 撤销/重做/合并/平滑 | ✅ **本轮新增** | 见下 |
| 五.1 数据中心分类 | ✅ 已有 | DataManagerView |
| 五.2 备份与恢复 | ✅ 已有 | 项目配置快照（非完整备份，UI 按 AGENTS §6 命名） |
| 五.3 批量数据处理 + 台账报表 | ✅ 部分 | 台账 XLSX 已有（ledger.py）；批量删除/重命名延后 |
| 五.4 权限分级 | ⏸ 本期不做 | AGENTS §9 排除完整 RBAC |

## 二、本轮改动（8 提交）

### 1. 智能复核模块（三.3，甲方重点「自动识别精度」）

- **后端** `quality_review.py` + `GET /api/projects/<id>/classification-results/<rid>/quality-review`：
  地类构成统计（图斑数/面积 m²/占比）+ 细碎图斑疑似线索清单（阈值可调，默认 100 m²，上限 200 条）。
  面积链与项目地图同口径（area→TBTYMJ_1→TBTYMJ 回落，无属性走 WGS84 球面几何兜底）；
  复核对象 current 为空回落 auto。**边界：只做结构线索，不承诺识别准确率**（精度评估需真值）。
- **Miner**：解译历史行新增「智能复核」按钮 → 质检弹窗（指标卡/地类构成表/疑似清单/阈值重筛/去编辑器修订）。
- 口径（时间）收尾：审计 #1 既定方案的残余六处 ad-hoc `isoformat()` 收口 `to_utc_z`
  （jobs/classification_results/mine_traceability/project_map），快照 manifest 内原始时间例外保留。

### 2. GeoView 图斑编辑辅助（四.3）

- 撤销/重做：纯逻辑抽 `classificationEditorHistory.mjs`（手势前状态入栈、顶点拖拽 500ms 合并、
  栈深 50、基线化脏判定），Ctrl+Z/Y，保存即新基线；原「撤销本地修改」更名「放弃全部修改」。
- 合并选中：`@turf/union` 真溶解，仅同地类 ≥2 面；消除锯齿：`@turf/simplify` ≈1.1m 容差。

### 3. DXF 导出（四.1）

- `export_dxf.py`：最简 ASCII DXF R12，闭合 POLYLINE 实体，地类按图层绑定
  （CLASS_<code> + ACI 色号；矿山边界默认导出无地类属性统一 CLASS_NA 层），坐标 EPSG:4326。
- 白名单/默认要素/后缀三处接入 + Miner 导出按钮。

### 4. 推理链路审计事件（已知缺口收口）

- 发布成功：`inference_result_published`（任务粒度，无服务器路径）；
- 任务失败：worker 经项目域公共服务函数 `record_inference_failure` 记 `inference_failed`
  （取消不记；项目不存在静默跳过；审计故障不影响失败终态）；
- 契约链同步：ACTION_CODE_BY_EVENT_TYPE ↔ miner ACTIVITY_LABELS（15→17 码覆盖断言）。

## 三、验证

- **后端全量**：1589 passed / 40 skipped（含新增 test_quality_review 8 例、test_export_dxf 7 例、
  test_utc_time 5 例、推理审计 2 例）。
- **Miner**：95/95 + 构建绿（relay 1 例 + 事件码断言扩充）。
- **GeoView**：56/56 + 构建绿（editorHistory 8 例）。
- **GUI 黑盒复验**（真实栈，证据 docs/images/feature-iteration-20260929/）：
  - 智能复核：云南项目 713·2024 真实成果 → 指标卡（1 图斑/水体 2684.82 m²/细碎 0）；
    阈值调 3000 重筛 → 该图斑进疑似清单（面积/质心正确）。
  - DXF 导出：数据管理页导出 → 记录「DXF 已完成 artifact.dxf」，制品 3.2MB 合法 R12
    （SECTION/TABLES/LAYER/ENTITIES/POLYLINE/EOF），附件下载 ✓。
  - 编辑器：新按钮初始禁用态正确；画多边形 → 撤销回基线（保存禁用/重做启用）→
    重做恢复脏态 → 放弃全部修改回干净态（**未保存，项目数据无残留**）。

## 四、过程中发现并修复

- **to_utc_z 截断午夜**（本批引入，全量测试抓住）：`isinstance(datetime, date)` 恒真导致
  分支顺序错误，所有 datetime 序列化成 00:00:00Z。临时 worktree 在会话前提交复跑定位，已修 + 5 例守护。
- GUI 复验中「导出 DXF」按钮首点无效：Miner 多模块视图共存导致同名按钮双份，
  选择器命中隐藏的禁用按钮——功能本身无缺陷，测试脚本改可见性过滤。

## 五、未测项与遗留

- **双选合并/消除锯齿的 GUI 实测**：IAB 工具链无法合成 Shift+点击（修饰键限制），
  合并/锯齿按钮的门控（未选中禁用）与 union/simplify 算法（node 实测真溶解/有效简化）已验证，
  双选→合并的完整点击链待人工在浏览器复验一次。
- IMG/ENVI/JPG/PNG 影像格式扩充、模型热升级接口、数据管理批量删除/重命名：延后排期（见上表）。
- H3（service.py 拆分）/M2（spatial_status 双轨）/M3（BFF 塑形下沉）：结构项维持 fix-plan 延后清单。
- 未提交的 miner/batch_latest_yunnan.py、parse_yunnan_kml.py、mines_yunnan.json、imagery_latest/
  为数据抓取脚本与运行数据，不属应用模块，保持未跟踪（建议移出仓库或纳入 .gitignore，待定夺）。
