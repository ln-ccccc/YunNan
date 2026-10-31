# 真实 case 端到端测试记录：保存竞态与 ID 追踪（2026-10-31）

对应修复：`9957cf6` 保存竞态两处遗留（正式图斑 ID 同步 + 保存提交边界不可合并）及其前序 `49706d1`/`7854dc0`/`8757b85`。本文记录用**真实栈 + 真实数据**对上述修复做的一次完整端到端验证，含测试方法、全部过程截图与数据库核对。

**补充轮（同日第二轮）**：应用户要求补上"调用地物分类推理"环节——从 Miner 智能解译页真实发起推理，走完「推理 → worker 自动发布 → 自动矢量化生成新成果 → 对新成果竞态编辑」全链，见第 7 节。

## 1. 环境与方法

| 项 | 值 |
| --- | --- |
| 被测栈 | 本机 Docker 运行栈：`yunnan-frontend:3000`（GeoView）、`yunnan-backend:5008`（Flask/gunicorn）、`yunnan-mysql`（库 `paddle_rs`）；容器挂载主树 `D:\项目\YunNan`（main，含全部修复提交） |
| 生效方式 | 测试前 `docker restart yunnan-backend yunnan-frontend`（gunicorn 内存模块与前端现场构建均需重启加载修复代码） |
| 测试数据 | 项目 10 `real-data-retest-2026-10-27`（复测专用项目，active）· 成果 111（fid 11195/2024）：基线 v0 auto(130 面) → v1 manual(130) → v2 manual(130)，全部 `auto-111-*` ID |
| 驱动方式 | Playwright 真实 Chromium（headed），真实登录页 admin 登录（密码取根 `.env` 的 `ADMIN_PASSWORD`，不落日志）、真实鼠标点选/改类/绘制、真实后端处理全部请求 |
| **唯一干预** | 对 `POST /api/projects/10/classification-results/111/revisions` 做 **3.5 秒透明延迟**（`route.continue()` 原样透传请求与响应）——真实后端响应为毫秒级，人工无法命中"保存期间继续编辑"的竞态窗口；除延迟外**不 mock 任何内容** |
| 脚本与产物 | 脚本 `D:/Temp/yunnan-fix-20261031/real-case/real-case-race.cjs`；截图 `…/real-case/screenshots/S01…S13.png`；结果 `…/real-case-result.json` |

## 2. 修复前置确认（真实栈）

重启后先验证后端错误分型修复已生效（curl 直打真实后端）：

| 请求 | 修复前 | 实测 |
| --- | --- | --- |
| POST `/api/projects/10/exports` 空 body（JSON 头） | 500 | **400** |
| 同上，body=`{` | 500 | **400** |
| 同上，`Content-Type: text/plain` | 500 | **415** |
| 同上，body=`null` | 500 | **422** |

## 3. 测试过程（每步截图）

| 步骤 | 截图 | 过程与断言 |
| --- | --- | --- |
| S1 | `S01-login-page.png` `S02-after-login.png` | 打开 :3000 真实登录页，admin 真实登录（真实 session cookie） |
| S2 | `S03-editor-loaded.png` | 打开成果 111 编辑器：工具栏"当前版本已保存"、版本记录 V0-V2(130 面)、真实项目底图瓦片渲染 |
| S3 | `S04-select-feature.png` | 真实鼠标点选既有图斑成功（WebGL 拾取，命中点 920,657） |
| S4 | `S05-change-class.png` | 改类为林地，出现"存在未保存编辑" |
| S5 | `S06-draw-new-feature.png` | 在命中点附近绘制新面 1（约 1.6m 微型面，提交时以 `client-*` 请求正式 ID） |
| S6 | `S07-save-pending.png` | 点保存 → 请求进入 3.5s 延迟窗口；**截图实锤：保存按钮 loading、六个改类按钮全部禁用**（保存冻结） |
| S7 | `S08-edit-during-save.png` | 挂起期间通过绘制控件真实绘制第二个新面——编辑未被拒绝（绘制控件不冻结，由代次+快照双比对兜底） |
| S8 | `S09-race-response-warning.png` | 延迟放行、真实响应返回：**warning"已保存为 V4；保存期间又有新的修改，请再次保存。"**；画布未被覆盖、V4(132 面)落库、撤销/重做按钮可用 |
| S9 | `S10-undo-works.png` `S11-redo-restore.png` | **撤销可用并成功回退一步**（`markSubmitBoundary` 提交边界生效，未清栈）；重做恢复 |
| S10 | `S12-second-save-ok.png` | 再次保存：**成功"已保存为 V5"（133 面）**，工具栏回到"当前版本已保存"；版本记录呈现完整链 V0→V5 |
| S11 | `S13-zoom-in-6x.png` | 真实底图连续放大 6 次：请求瓦片最高 **z15**（真实 manifest 声明的 max_zoom），**越级 404=0**、无失败请求 |

首轮排障记录（无数据写入）：第一版脚本用画布中心偏移 ±60px 画面，被服务端 422 拒绝（fid 11195 的 ROI 仅约 150m×90m，`roi.covers` 校验正确拒绝越界面）——据此把新面改为"点选命中点附近微型面"后一次通过。422 为预期校验行为，未产生脏数据。

## 4. 数据库核对（paddle_rs.classification_revisions）

测试后导出 v3/v4/v5 修订的 `feature_collection_json` 逐版对比 `feature_id`：

| 修订 | 面数 | ID 构成 | 相对上一版 |
| --- | --- | --- | --- |
| v3（第一轮竞态保存） | 131 | 130×`auto-111-*` + 1×`manual-111-*` | 新面获正式 ID |
| v4（本轮竞态保存） | 132 | 130×`auto-111-*` + 2×`manual-111-*` | 新增 `manual-111-ec12ed2f…`（新面 1）；**v3 的 ID 零丢失** |
| v5（再次保存） | 133 | 130×`auto-111-*` + 3×`manual-111-*` | 新增 `manual-111-668f6fd7…`（新面 2）；**v4 的 ID 零丢失** |

- **`client-*` 残留：0**——竞态分支同步正式 ID 后，再次保存提交的全部是服务端正式 ID，跨版本图斑追踪无断裂。
- 导出文件：`D:/Temp/yunnan-fix-20261031/real-case/rev{3,4,5}-export.json`。

## 5. 结论

真实栈、真实账号、真实数据下完整复现"保存未返回时继续编辑"竞态：修改不丢失（画布保留+warning）、撤销/重做可用、正式图斑 ID 跨版本稳定、底图缩放不越级——三项修复（P1 竞态、P2 ID 同步/提交边界、P2 zoom）与后端错误分型在真实环境全部生效。

## 6. 遗留与说明

- 本轮在成果 111 上留下 v3/v4/v5 三版真实修订（不可变追加设计，无删除接口）；项目 10 为复测专用项目，与 2026-10-27 复测留痕同类。如需清理走项目删除/快照流程，另需负责人确认。
- 透明延迟代理仅作用于保存 POST 的传输时机，请求/响应内容与真实后端交互完全一致；这属于时序控制手段而非 mock，结论代表真实链路行为。
- 截图与导出 JSON 位于 `D:/Temp/yunnan-fix-20261031/real-case/`（仓库外，不入库）。

## 7. 补充轮：从发起推理开始的完整闭环（同日第二轮）

第一轮测试从既有成果（10-27 复测留存的 result 111）起步，未覆盖推理环节。本轮补齐：**真实发起地物分类推理 → worker 自动发布 → 自动矢量化生成新成果 → 对新成果执行同一套竞态编辑测试**。

### 7.1 方法

| 项 | 值 |
| --- | --- |
| 入口 | Miner 智能解译页（:4000 `#/interpretation`）→「发起新解译」→「项目地物分类」弹窗 |
| 推理影像 | 「大理2024真实影像-复测1027(573M)」（dataset 74，10-27 复测上传的真实影像；项目内共 3 个就绪影像资产） |
| 设备 | 自动（优先 NVIDIA GPU）；推理范围由影像范围 ∩ 项目激活矿山矢量自动求交（**8 个矿山**，无手动 FID 选择） |
| 脚本 | `D:/Temp/yunnan-fix-20261031/real-case/real-case-inference.cjs`；除保存 POST 透明延迟外零 mock |
| job | `d3dae669-b199-4cd0-9300-de82f5c74735` |

发布机制说明：推理 succeeded 后由 **inference worker 自动调用** `publish_project_inference_result`（`backend/applications/inference/worker.py:460-480`），PNG 产物落盘、每个写入 FID 在 `classification_results` 插行并**同步自动矢量化**（revision 0 = auto，`vector_status=ready`）——全链无人工发布步骤。

### 7.2 过程截图（R01-R17）

| 步骤 | 截图 | 内容 |
| --- | --- | --- |
| R1 | `R01/R02` | Miner 真实登录 → 项目工作台 |
| R2 | `R03-R06` | 智能解译页选项目 → 推理弹窗（影像下拉=3 个就绪资产）→ 选影像 → 「确认开始推理」提交 |
| R3 | `R07/R08/R09` | 轮询真实状态 queued → running → **succeeded 8/8**（8 矿山全部推理成功）；历史刷新出 8 条记录（每矿山原图+分类预测 PNG） |
| R4 | — | API 确认新成果：fid 11195 → **result_id=119，vector_status=ready，V0=130 面**（自动矢量化成功） |
| R5 | `R10-R12` | 新成果 119 编辑器：点选/改类/绘制新面 1（client-*） |
| R6 | `R13-R15` | 保存透明延迟 → 挂起期间绘制新面 2 → 放行：**warning"已保存为 V1；保存期间又有新的修改，请再次保存。"**、画布保留 |
| R7 | `R16/R17` | 撤销+重做可用（提交边界）；再次保存 **成功为 V2（132 面）** |

关键帧：`R08-inference-done.png`（8 矿山推理产物）、`R15-race-warning.png`（**V0 自动基线 130 面 · 2026/10/31 17:35:11 = 本轮推理产生**，V1 竞态保存 131 面）。

### 7.3 数据库核对（result 119，导出 `rev119-{0,1,2}-export.json`）

| 修订 | 面数 | ID 构成 | 相对上一版 |
| --- | --- | --- | --- |
| v0（本轮推理自动矢量化） | 130 | 130×`auto-119-*` | 推理产物 ID 命名空间全新（`auto-119-*`，与 result 111 的 `auto-111-*` 完全隔离） |
| v1（竞态保存） | 131 | 130 auto + 1 manual | 新增 `manual-119-ca386ddc…`（新面 1）；v0 ID 零丢失 |
| v2（再次保存） | 132 | 130 auto + 2 manual | 新增 `manual-119-b65859e9…`（新面 2）；v1 ID 零丢失、`client-*` 残留 0 |

### 7.4 补充轮结论

从「用户点击确认开始推理」到「竞态编辑后正式 ID 跨版本稳定」全链真实环境闭环：真实 GPU/推理容器处理 8 矿山影像、worker 自动发布与矢量化产出全新成果（130 图斑）、编辑器对该成果的竞态保护与 ID 同步全部生效。

## 8. 补充轮二：解译平台入口（GeoView Segmentation 页）

分类推理有两个产品入口，第 7 节走的是 Miner 项目工作台入口；本节实测 **GeoView 解译平台入口**的差异化链路（现场上传 → 影像收编 → 同一 job 队列）。

### 8.1 两入口架构（代码核实）

| | Miner 智能解译页（InferenceModal） | GeoView 解译平台（Segmentation 页） |
| --- | --- | --- |
| 定位 | 项目工作台：项目维度正式推理流程 | 解译工具页：现场上传影像的快速分析 |
| 影像来源 | **只能引用已登记**的项目影像资产（dataset_id，须 imagery+ready） | **现场上传 tif**（必须填年份），不依赖预登记 |
| 请求接口 | `POST /api/inference/jobs`（BFF 转发） | `POST /api/analysis/kml_roi_inference`（`analysis.py:363` → `prepare_geoview_interpretation`，`inference/interpretation.py:77`） |
| 模式分叉 | 恒为项目模式（can_start_inference 前置门槛） | 影像 ∩ 项目矿山相交 → **项目模式**：上传影像被收编进项目输入（`_publish_project_input`）后构造与 Miner 完全相同的 job 请求 `create_job`；**不相交/无 project_id → standalone**：同步出结果、只在解译平台页内展示，不建 job、不产生分类成果 |
| 后续链路 | `create_job` → 同一 `inference_jobs` 队列 → 同一 worker → **同一套自动发布+矢量化**（worker 只认 job，不区分来源入口） | 同左（项目模式时） |
| 另有两个同义入口 | 地图工作台「+ 添加矿山与解译」、项目工作台「开始地物分类」——与智能解译页共用同一 InferenceModal，属同一入口的三个摆放位置 | — |

### 8.2 实测过程（G01-G04）

| 步骤 | 截图 | 内容 |
| --- | --- | --- |
| G1 | `G01-segmentation-page.png` | GeoView 登录后打开 `/segmentation?project_id=10`（带项目参数） |
| G2 | `G02-uploaded.png` | **现场真实上传** 483K 切片 tif（`slice_20261027_064224_5f0120_r0c0.tif`，裁自本项目矿山 696）+ 填年份 2024 |
| G3 | `G03-job-created.png` | 响应实证 `mode=project`、`matched_fids=[696]`、job `aaee987b…` 建立——上传影像被收编进项目输入 |
| G4 | `G04-inference-done.png` | 推理 succeeded 1/1 → worker 自动发布+矢量化；解译平台页按契约不展示项目成果明细 |

### 8.3 结果核对

- job `aaee987b-5656-4fad-b1e2-f6bbbabc5e4d`：succeeded 1/1。
- 新成果 **result_id=120**（project 10 / fid 696 / 2024 / ready / V0 / **146 面**）——与 10-27 复测的 result 112（同一影像切片、同一 ROI）同为 146 面，自动矢量化确定性输出一致。
- 两个入口殊途同归：GeoView 上传的影像经收编后，与 Miner 入口共享 job 队列、worker、发布与矢量化链路，产出的分类成果形态完全一致（本轮共产生 result 112/119/120 三个 ready 成果，全部可"编辑矢量"）。

### 8.4 测试入口选择说明

第一、二轮测试选 Miner 入口的原因：测试目标是「推理 → 分类成果 → 编辑竞态」闭环，Miner 入口引用已登记影像直接命中该链（解译平台 standalone 模式不产生分类成果，走它测不到编辑环节）；解译平台入口的差异化部分（现场上传、影像收编 `_publish_project_input`、mode 分叉）由本节补测覆盖。

## 9. 补充轮三：mine-imagery-gehi skill 抓取历史影像推理（数据丰富）

应用户要求，用 [mine-imagery-gehi skill](https://github.com/ln-ccccc/Zhong-s_Skills/tree/main/mine-imagery-gehi)（批量获取谷歌地球矿山影像，按 KML 多边形逐块下载 GeoTIFF）为项目 10 补充**2018 历史期**真实影像并完成推理，形成 2018/2023/2024 多年数据。

### 9.1 管线执行（skill 7 步）

| 步骤 | 结果 |
| --- | --- |
| 工具部署 | GEHistoricalImagery v0.7.1 → `C:/tools/GEHistoricalImagery`（新版 exe 在 `gdal/` 子目录，skill CONFIG 相应修正） |
| KML 解析 | 项目 10 激活矢量 `mines/17/source.kml`（1130 图斑）→ `parse_kml_polygons.py` → 565 矿清单，8 绑定矿山命中 |
| 可用年份诊断 | 8 矿山档案并集：2012/2014/2017/2018 全有（**2022 无档案**——首版 2022 窗口 0/8，据实切 2018） |
| 下载 | 2018-03 期（696/713/714/715/716/718=2018-03-02；1330/11195=2018-03-19），coverage 全部 ≥0.999，**8/8 零失败**，产物 526×592 RGB deflate（~1MB/幅） |

### 9.2 推理与数据丰富

8 幅影像逐幅 `POST /api/file/upload` → `POST /api/analysis/kml_roi_inference`（year=2018, project_id=10）→ **8/8 job succeeded**（相邻矿山被一幅影像同时求交命中，成果行幂等新增）。项目 10 成果分布变为：

| 矿山 | 2018 | 2023 | 2024 |
| --- | --- | --- | --- |
| 696 | 1 行 | 1 行 | 10 行 |
| 713/714/715 | 各 3 行 | — | 各 7 行 |
| 716/718/1330/11195 | 各 2 行 | — | 各 5 行 |

### 9.3 多年形态实证（截图 M01-M04）

- 矿山 696「地物分类」：**2018/2023/2024 三张年份卡** + 历年地类占比表三列——**裸地 85.9%(2018) → 4.0%(2023) → 4.0%(2024)、草地 4.5% → 22.7% → 22.7%、林地 0.9% → 10.5% → 10.5%**（矿山治理的真实跨年变化）；
- 变化矩阵自动切换最近两年（2023→2024，审校口径 basis=revised_vector）；
- 「原始影像」按年分组三条+，每条带预览/下载按钮；
- 趋势报告首末年变为 2018 vs 2024（跨 6 年）。

脚本与产物：`D:/Temp/Zhong-s-Skills-20261011/mine-imagery-gehi/scripts/`（mines_yunnan_p10.json、batch_yunnan_p10_2022.py）与 `D:/Temp/gehi-yunnan-2018/`（8 幅 tif、manifest、batch-infer-2018.cjs、infer-results.json）。

## 10. 补充轮四：单幅多矿山影像推理测试（2014 期）

应用户要求验证「选一张影像包含若干矿山，直接送进推理」：选取 **713 幅**（bbox 缓冲后覆盖 713/714/715 三个相邻矿山），用 skill 管线抓 **2014-10-30** 期（coverage 完整）→ 上传推理（year=2014）→ job `c2599e31` succeeded。

- `mode=project, matched=[713,714,715]`——**一次推理、三矿山各得一期成果**（id 140/141/142，75/18/3 面，同一影像按各矿山 ROI 分别裁剪矢量化）；
- 矿山 713 形成 **2014/2018/2023/2024 四年**，历年占比表四列：裸地 14.0(2014) → 23.5(2018) → 1.8(2024)（2018 开采高峰、2024 治理后骤降）；
- 变化矩阵（2018→2024，审校口径 basis=revised_vector）出现真实地类演变：草地→水体 81.2%、林地→水体 79.7%、裸地→水体 84.1%——矿山 713 从开采/裸地状态演变为积水坑塘（2024 水体 85.9%），矩阵首次承载真实的矿山生命周期叙事；
- 截图：`V03-713-classification-trace.png`（四年卡片+四列占比）、`V03b-713-change-matrix.png`（2018→2024 转移矩阵）。

脚本与产物：`D:/Temp/gehi-yunnan-2014/`（713_201410.tif、batch-infer-2014.cjs）。
