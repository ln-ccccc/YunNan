# 矢量编辑几何链路：架构审计（2026-11-24）

审计对象：分支 `fix/pr1-import-context` 的 `0361d52` 加当时未提交的前端几何改动（即生产当时运行的 19:10 构建）。
方法：只读取证——源码静态盘点（后端、GeoView、Miner 三侧）、在生产后端容器内核实运行时版本、把全部生产成果只读导出后离线分析。
本文只陈述事实与处置归类；数字见 [对照实验](geometry-engine-simplify-ablation.md) 与 [定稿回归](geometry-engine-regression-20261124.md)。

## 1. 结论

问题不是个别坏例子，而是同一个合法性问题上存在三套互不相同的答案，且没有一套是权威的：

- **越界判定**：后端保存用 GEOS `covers`（带 1e-9 度容差）；前端提示最初用 turf `booleanWithin`（无容差，生产 144,176 个图斑里 48,287 个被误报越界），后来改成采样点检查（不再误报，但只看有限个点，会漏掉细缝）。
- **有效性**：后端用 GEOS `is_valid`；前端另有一份手写的 JavaScript 版本，它的注释里写着漏掉嵌套外壳，而且是对着 Shapely 2.1.2 / GEOS 3.13.1 核对的——生产运行时是 Shapely 1.8.5 / GEOS 3.10.3。
- **简化**：前端用 turf 的不保拓扑 Douglas-Peucker，再用前端自己那套判定决定接受还是跳过；贴矿山的图斑简化后留下厘米级细缝，到保存时才被后端 422 拒绝。

处置方向：合法性判定、简化、合并只在后端一处计算，以运行时自带的 Shapely / GEOS 为权威；保存、编辑期校验、简化、合并共用同一个判定函数。实现见 `backend/applications/project_hub/geometry_engine.py`。

## 2. 调用链

```
Miner  EditingView / SmartInterpretationView
  └─ buildGeoViewEditorUrl → 新窗口打开 GeoView /classification-results/editor?project_id=&result_id=
GeoView  ClassificationResultEditor.vue（MapLibre GL + mapbox-gl-draw 1.5.1）
  ├─ GET  /api/projects/{pid}/classification-results/{rid}            详情：当前版本、roi_geometry、map_manifest
  ├─ GET  /api/projects/{pid}/classification-results/{rid}/revisions   修订历史
  ├─ POST /api/projects/{pid}/classification-results/{rid}/revisions   保存（直连 Flask，不经 Miner BFF）
  └─ POST /api/projects/{pid}/classification-results/{rid}/export      导出已保存版本
Flask    save_classification_revision
  └─ 状态检查 → _validate_save_payload（结构 / 容量 / ID / 有效性 / ROI）→ 乐观锁认领当前版本
     → 写修订统计 → 修订与审计记录 → 快照文件（临时文件 + os.replace）→ 提交 → 后台重建变化矩阵
推理成果  publish_classification_result → vectorize_label_geotiff → label.tif、auto.geojson、Revision 0（不经保存校验）
消费方    classification_stats（矢量面积、变化矩阵）、mine_traceability、project_map、quality_review、导出
```

- GeoView 直接请求 Flask；Miner BFF 只转发成果清单和质检。
- Miner 不下载图斑几何，地图上画的是矿山边界（`GET /api/projects/{id}/geojson`），没有任何多边形几何运算。

## 3. 重复实现清单（改造前）

| 判定 | 实现数 | 位置 | 口径差异 |
| --- | --- | --- | --- |
| ROI 覆盖 | 3 | 后端 `classification_results._validate_save_payload`；前端 `roiViolation`（`classificationEditorGeometry.mjs`）；最初的 `booleanWithin` | 后端是 buffer 覆盖；前端是采样点加距离容差，注释里自认漏“图斑盖住 ROI 的洞”和窄凹口；`booleanWithin` 无容差 |
| 面有效性 | 3 | 后端 `is_valid`；前端 `polygonGeometryProblems`（手写自交、嵌套、共线检测）；结构级 `polygonStructureProblem` / `buildEditableFeatureCollection` | 手写版自认漏嵌套外壳，且核对时用的 GEOS 版本与生产不同 |
| 点在面内 | 4 | `classificationEditorGeometry.mjs` 两处、`classificationEditor.mjs`（MapLibre 填充层查询）、Miner `MapContainer.vue`（Leaflet） | 仅用于提示与命中测试 |
| 简化 | 1 套库 + 2 层自检 | turf `simplify`（`highQuality: false`，不保拓扑）+ `simplifyPolygonSafely` 降容差重试 + 前端有效性与 ROI 自检 | 接受与否由前端判定，与后端规则不一致 |
| 合并 | 1 套库 + 结构检查 | turf `union` + `polygonStructureProblem` | 不做完整有效性与 ROI 检查 |
| 面积 | 后端 2 + 前端 1 | `_geometry.geometry_area_m2`（扣洞）；`api/stats.py`（只取外环，不扣洞）；前端屏幕鞋带公式（仅命中排序） | 后端两套球面公式口径不一致 |
| 环闭合判断 | 5 处内联 | `classificationEditorGeometry.mjs` 4 处、`classificationEditor.mjs` 1 处 | — |

容差常量散落在：后端 `ROI_COVER_EPSILON_DEGREES = 1e-9`；前端采样 `1e-9`（容差）、`1e-5`（采样间距）、64（分段上限）、2000（补点上限）；简化 `1e-5`；未提交的边界吸附 `1e-8`；矢量化 `precision=10`（面）与 `precision=6`（ROI 掩膜）；连通域下限 16 像素。这些数是六种不同含义的东西，此前没有一处说明它们各自是什么。

## 4. 运行时核实（生产后端容器）

| 项 | 值 |
| --- | --- |
| Python | 3.10.20 |
| Shapely | 1.8.5.post1（`backend/requirements.txt` 锁定） |
| GEOS | 3.10.3-CAPI-1.16.1 |
| rasterio / GDAL / numpy | 1.4.4 / 3.10.3 / 1.26.4 |
| 镜像 `yunnan-runtime:current` | `sha256:03b45aa874b72059efa875dc06b37d15f64f092baec5bf24aadf69c36e278502` |

与常见假设不同、会影响实现的三点：

- 没有 `coverage_simplify` / `coverage_is_valid` / `coverage_invalid_edges`（GEOS 3.12 才有），也没有 Shapely 2 的模块级函数。
- `STRtree.nearest()` 返回几何对象本身而不是下标；`STRtree.query()` 返回的不保证是传入的同一个对象。
- `PreparedGeometry` 没有 `distance`。

前端几何模块注释里的“shapely 2.1.2 / GEOS 3.13.1”不是生产环境。

## 5. 生产数据是不是 polygon coverage

**不是，规则上也不要求是。**

- 契约（`docs/vector-result-v1-contract.md`）只约束单个图斑：非空、有效的 `Polygon` / `MultiPolygon`，完整位于成果 ROI 内。对图斑之间的重叠与缝隙没有任何规定，后端也没有任何此类校验。
- 自动成果按构造互不重叠（每个像元只归一个连通域），但有空隙：ROI 外与 nodata（255）像元、小于 16 像素被静默丢弃的连通域、取面失败的域，都不出图斑。
- 人工修订可以自由制造重叠与缝隙；变化矩阵用 `rasterio.features.rasterize` 的默认合并方式，重叠处由列表中靠后的要素覆盖，结果依赖要素顺序。
- `MultiPolygon` 契约允许；项目 10 的 565 座矿山边界全部是 MultiPolygon，成果里的 ROI 也几乎全是。

实测规模（重叠对数、缝隙占比、公共边占比、无效矿山边界数）见定稿回归报告。本轮不改变这条规则：新增重叠 / 缝隙约束会改变既有成果的验收规则，属于产品决定。

## 6. 编辑操作：输入、输出、不变量、边界条件

| 操作 | 触发 | 输出 | 不变量 | 边界条件 |
| --- | --- | --- | --- | --- |
| 绘制新面 | `draw.create` | 新要素，地类取当前选中类，`feature_id` 为 `client-*` | 有效、位于 ROI 内 | 草稿不足 3 点不能完成；Esc 退出；绘制中撤销等于丢弃草稿 |
| 拖动 / 增删顶点 | `draw.update`（鼠标释放、增删顶点时各一次） | 几何改变的要素 | 有效、位于 ROI 内 | 删顶点受“外环至少 3 个顶点”防护；拖动可造成自相交，此前要到保存才被发现 |
| 整体拖动、框选批量拖动 | `simple_select` 拖动 → `draw.update` | 一个或一批要素 | 同上 | 一次手势一个事件，事件里带全部被移动的要素 |
| 重分类 | 点击地类 | 只改属性 | — | 批量改类后合并按钮状态同步 |
| 删除 | Delete / 垃圾桶 | 要素移除 | — | 进入撤销历史 |
| 合并 | 工具栏 | 同类 ≥2 个面合成一个（`Polygon` 或 `MultiPolygon`） | 有效、位于 ROI 内 | 新的画布 id，`feature_id` 另议；此前不做完整有效性与 ROI 检查 |
| 消除锯齿 | 工具栏 | 选中面顶点减少 | 有效、位于 ROI 内；面积变化有界；不改未选中的面 | 可以合法跳过并保持原样 |
| 保存 | 工具栏 | 新修订（乐观锁） | 服务端规范化属性与 ID；容量上限只约束增长 | 409 不覆盖他人修改；422 定位到出错的面；保存期间的新修改保留 |
| 导出 | 工具栏 | 已保存版本（服务端）或草稿（客户端） | 草稿与保存提交同一套规范化 | — |

改造前，上表“有效、位于 ROI 内”这一条只有保存在后端真正执行；其余操作在前端靠那三套不一致的判定给提示。

## 7. 容差三分

| 名称 | 值 | 含义 | 能不能用来越界判定 |
| --- | --- | --- | --- |
| 坐标精度 | 1e-10 度 | 自动矢量化写坐标时的舍入单位 | 不是容差 |
| 数值容差 | 1e-9 度（约 0.1 毫米） | 只吸收坐标舍入与投影换算误差 | 是，且是唯一允许的容差；像元量级（≥1e-6 度）的真实越界仍被拒绝 |
| 简化容差 | 1e-5 度（约 1.1 米） | 用户可见的“消除锯齿”力度 | 否，与合法性无关 |
| 像元分辨率 | 每条成果 0.1–0.7 米不等 | label 栅格属性 | 否 |

真实数据上贴矿山的图斑与边界的偏差在 1e-10 度量级，细缝在 1e-7–1e-6 度（1–11 厘米），像元在 1e-6–1e-5 度。三个量级分得很开，不存在“容差该取多大”的灰色地带；之前的问题是用采样和无容差判定，而不是数值取得不对。

## 8. 发现与处置

| 编号 | 发现 | 影响 | 处置 |
| --- | --- | --- | --- |
| F1 | ROI 覆盖判定三份实现 | 误报或漏检 | 本轮：统一到 `geometry_engine.evaluate`，完整拓扑关系判定 |
| F2 | 面有效性三份实现，前端版本不完整且对错 GEOS 版本 | 前端提示与后端结论可能相反 | 本轮：只保留 GEOS `is_valid`，删除前端手写版 |
| F3 | 简化用不保拓扑 DP，接受与否由前端判定 | 贴边界图斑留下细缝，保存被 422 | 本轮：简化改走后端并以同一判定验收；算法取舍由对照实验决定 |
| F4 | 运行时 GEOS 3.10.3 没有 coverage 函数 | 无法直接用 coverage_simplify | 独立评估 Shapely 2.1.2 / GEOS 3.13.1，暂不升级依赖 |
| F5 | 没有图斑间重叠 / 缝隙约束 | 规则现状，不是缺陷 | 明确写入契约；是否约束属于产品决定 |
| F6 | 自动成果发布不经保存校验 | 发布路径与保存路径的规则不一致 | 未改，列入风险 |
| F7 | 矢量化里 ROI 面用 `precision=10`、掩膜用 `precision=6` | 两处不严格是同一个多边形 | 未改，列入风险 |
| F8 | 小于 16 像素的连通域静默丢弃 | 自动成果天然有缝隙 | 未改，列入风险 |
| F9 | 矿山边界导入没有有效性检查或修复 | 无效边界会让 `covers` 抛异常 | 未改；实测无效数见回归报告 |
| F10 | 面积两套球面实现，一个扣洞一个不扣 | 看板与统计口径可能不同 | 超出本轮范围，列入风险 |
| F11 | 变化矩阵栅格化默认后者覆盖 | 有重叠时结果依赖要素顺序 | 超出本轮范围，列入风险 |
| F12 | 保存每个请求重算一次 `roi.buffer(1e-9)` | 重复计算 | 本轮：ROI 外扩多边形按成果缓存 |
| F13 | 契约写的是「ROI 判定不允许 buffer、容差」，实现早已按 1e-9 度数值容差放行 | 文档与宣称不一致 | 本轮：契约订正为与实现逐字一致，并写明图斑之间无重叠 / 缝隙约束这一规则现状 |
| F14 | GEOS 3.10.3 的拓扑保持简化对约 0.02%–0.03% 的退化输入不确定（同一输入不同输出） | 简化结果与回归计数的末位不可复现 | 校验兜底保证两种输出都合法；记录为已知限制，DP 作为确定性的替代方案并列出数据。见对照实验 §7 |
| F15 | 默认简化容差 1e-5 度（约 1.1 米）相对图斑过小：被简化的图斑 85% 小于 100 m²，面积中位变化 15% | 「消除锯齿」会让小图斑明显失真 | 本轮未改参数（属于产品决定）；已把已部署方案的 37.5% 降到 15.2%，数据见对照实验 §6 |
