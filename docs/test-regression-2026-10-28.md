# 全量回归测试证据（2026-10-28）

对应基线：main@77e643e（= [real-data-retest-2026-10-27.md](real-data-retest-2026-10-27.md) 修补终点，10 提交 `6b5f6d7..77e643e` 全部落地后的固化回归）。
执行环境：本机 Docker 栈七容器（backend/frontend/miner-api/miner-web/inference-worker/spatial-worker/mysql，全部 healthy）+ RTX 5060 8GB；backend 容器与宿主工作树为同一 bind mount（代码即 main@77e643e）。
留痕目录：`D:/Temp/yunnan-test-1028/`（原始日志、脚本、截图，不入仓；本文档为入库摘要）。

## 1. 静态测试套件

| 套件 | 命令 | 结果 | 日志 |
| --- | --- | --- | --- |
| 后端全量 discover | 容器内 `python -B -m unittest discover -s . -p 'test_*.py'` | **1996 例：failures=0，errors=1（仅 §3.2 环境性伪影），skipped=53**（386.7s） | `backend_final_regression.log`、`backend_unittest_full.log` |
| 后端审查标准显式集 1 | `test_project_api test_project_read_models test_project_spatial test_project_map` | 102/102 OK（12.9s） | `backend_final_regression.log` |
| 显式集 2 | `test_classification_vectorization test_classification_results test_classification_result_api` | 33/33 OK（3.0s） | 同上 |
| 显式集 3 | `test_file_upload_chunks test_imagery_processing test_project_inference_results` | 326/326 OK，skipped=28（77.8s） | 同上 |
| 显式集 4 | `test_security_recheck test_auth_api test_auth_change_password` | 103/103 OK（18.2s） | 同上 |
| 显式集 5（本批改动模块） | `test_inference_device test_inference_jobs test_inference_runner test_project_indices_aggregate test_project_map` | 179/179 OK（19.4s） | 同上 |
| 离线部署契约 | 宿主 `python -B -m unittest test_yunnan_offline_deployment` | 19/19 OK | `offline_deployment_test.log` |
| Miner 单测 | `npm test` | 128/128 OK（13.2s） | `miner_npm_test.log` |
| Miner 构建 | `npm run build` | OK（9.95s） | `miner_build.log` |
| GeoView 单测 | `npm test` | 58/58 OK | `frontend_npm_test.log` |
| GeoView 构建 | `npm run build` | OK | `frontend_build.log` |

## 2. 活体栈回归（生产构建，真实数据）

**API 链路**（容器内 urllib 会话直打 Flask）：login → session → projects → P1 overview → map/manifest → inference/capabilities 全 200（9–824ms）。

**本批 10 修复的活体复验**：

| 修复 | 活体探针 | 结果 |
| --- | --- | --- |
| J-1 缺失瓦片 200+透明 PNG | BFF `/tiles/projects/1/2/{z}/{x}/{y}.png` 三坐标（覆盖内空洞+界外） | 全部 200，`PNG 1x1 8-bit RGBA`（70B） |
| J-2 stats 提速+缓存 | P1 stats 两连击（容器空闲时） | 首击 2752ms / 缓存 2ms（与修补时 2.3s/0ms 一致；并行压测期首击 9.6s 系 CPU 抢占，非退化） |
| J-3 缺 fid 400 | `/mines/indices` 无参 | 400「缺少参数 fid」 |
| K-1 趋势报告激活 | P1 `/mines/trend-report` | 返回完整结构（available/class_trends/tables/mine_total…） |

（J-0/J-4/K-2/K-3/L-1/L-3 由单测+上轮真实 UI/任务证据覆盖，本轮未重放重型任务链。）

**UI 冒烟**（Playwright 生产构建 :4000/:3000，1600×900）：Miner 登录→项目列表→工作台概览→P1 地图（canvas 像素采样非空）→解译页 5/5 步通过；GeoView 登录→/segmentation 跳转通过；**全程 console error=0、坏请求=0（含瓦片 404）**。截图 `shots/01..06*.png`。

**数据核验**：P1 readiness 5/5 ready（档案/边界/激活底图/推理影像/可审成果全 passed）。

## 3. 本轮发现并处置的问题

### 3.1 测试隔离缺口（本批 bdfbcc4 引入，已修复）

全量 discover 下 `test_project_map.TestProjectStatsAreaAggregation` FAIL（mineAreaTotal 0.0），单独运行通过。定位：J-2 引入的模块级 `_STATS_CACHE`（TTL 120s，按 project_id 键）在同进程内跨测试模块存活——先跑的 `test_project_indices_aggregate` 用同名自增 project id 命中缓存，后者读到前者的空结果。**修复**：受影响测试类 setUp 显式 `invalidate_project_stats_cache()`（生产路径写入时已有失效调用，行为不变；纯测试隔离修复）。复现组合（两模块连跑）修复前 FAILED(failures=1)、修复后 OK。

### 3.2 环境性失败（存量，非代码问题，未改动）

| 现象 | 根因 | 处置 |
| --- | --- | --- |
| `ERROR: model.custom_models`（mmcv 版本断言） | unittest discover 递归导入 `model/` 包，其 vendored mmseg 配置断言 mmcv<2.2.0，而 backend 容器为 2.2.0（推理容器为 2.1.0 不受影响；生产推理路径不走 backend 容器） | 已知 discover 范围伪影；审查标准入口（显式模块清单）不触发。如需 discover 全绿可后续把 model/ 移出 discover 范围，本轮不动 |
| 首轮 discover 中 6 例 fixture/env FileNotFoundError | 容器只挂载 `backend/`，仓库根 `tests/fixtures/` 与 `image_bundle.env` 不在容器内 | 证据采集时 `docker cp` 临时补入后全过（94/94, skipped=10）；属运行环境差异，非代码缺陷 |

### 3.3 遗留测试数据清理

上轮复测文档声称 N6 临时项目「16–19 全部清理」，实际 **16/17（n6-batch-1/2，2026-10-27 14:31 建，0 矿山 0 数据集）漏清**。本轮逐一显式 ID 核验（名称+内容）后 archive→delete，四步全 200；终态项目清单回归 1/2/4/10。教训与批量删除规程同源：清理后必须复核清单。

## 4. 阶段二：重型真实任务链重放（同日追加）

上轮仅留证据、本轮未重放的重型链路全部在活体栈重放，另补从未深测过的盲区。脚本与日志：`D:/Temp/yunnan-test-1028/phase2/`。

| # | 项 | 方法 | 结果 |
| --- | --- | --- | --- |
| A | GPU 推理全链重放 | P10×数据集74×year2024（路由匹配 8 个影像相交 FID）→ succeeded 42s，成果 130 要素落库（result 111） | ✅ |
| A | 推理期并发读压测 | overview/assets 双线程持续打（47+49 次） | 零失败，p50≈315ms |
| B | 取消语义三级定性 | ①取消+0.02s→**cancelled**；②+3s/③+21s→succeeded | ①窗口内取消被尊重；②③落入上轮已记录"已受理仍完成"竞态（预热模型下 8-FID 任务前发布窗口仅约 4s，docker 日志时间戳证明推理在创建后 0.3s 内完成装载）——**非回归**，K-4 已决策不做 |
| B+ | search 端点压测修正 | `q=15`（精确 FID）×29 次 | 零失败（首轮 `q=7` 51/51"失败"系探针不合业务语义：q 需精确匹配 FID，非缺陷） |
| C | 编辑修订链+乐观锁 | 按受限 FeatureCollection 契约：改类保存 rev0→1→2；双会话并发同基准 | ✅ 200+409「成果已被其他保存更新」；旧基准单发 409（真实 MySQL） |
| D | 质检重放 | result 111 quality-review | ✅ 200，5 类面积/占比/数量统计完整（water 65.67%/forest/grassland…） |
| E | 导出五格式深验 | 下载 artifact 后程序化读回 | GeoJSON 565/565 面积零偏差；CSV 566 行；**SHP 经 OGR 读回 565 要素面积 rel=1.7e-14**；XLSX 2 sheet；DXF 3.4MB 568 多段线含真实坐标；分类导出 200 |
| F | 快照三链重放 | P10 生成（manifest 565 矿山+11 数据集）→导入新项目→负例→清理 | ✅ 导入报告 mines=565/assets=11/failed=0；负例 422；**语义确认：导入会把 manifest 内项目名恢复到目标项目**（临时项目被重命名为 P10 同名，属预期行为） |
| G | MySQL 并发写压测 | 8 线程×(建→改名→复读→归档→删) | ✅ 全 8/8 一致、零异常、零残留（3.3s）——补上"规模化并发写"盲区 |
| I | 编辑器浏览器冒烟 | GeoView editor?project_id=10&result_id=111 | ✅ 底图+分类图斑渲染（视觉模型确认 V0-V2 版本记录、"当前版本已保存"）、API 修订在 UI 可见 |

### 4.1 阶段二新发现（非阻断，如实记录）

1. **POST `/api/projects/<id>/backups` 空 body → 500**（`request.json` 对空 body 抛 BadRequest 被 500 兜底，应 4xx）。空 body 探针触发；带 `{}` 正常。存量小缺陷，未修。
2. **GeoView 编辑器瓦片路由缺 J-1 平价**：底图直连 Flask `/api/projects/10/map-resources/20/tiles/17/...`，视窗缩放超过金字塔 maxZoom(z15) 时 404（一次载入 21 个）；地图仍正常渲染（截图确认），仅控制台噪音与冗余请求。J-1 透明瓦片只覆盖了 BFF 路由。未修。
3. **路由语义确认**：不传 `mine_fids` 时任务路由=影像相交矿山子集（P10×数据集74=8 个 FID），传超集被 400「必须是影像相交矿山的非空正整数子集」——行为正确，此前未在文档明示。

## 5. 未测项（如实声明）

- 11G 底图完整重切未重放（上轮已有真实证据；瓦片透明化 J-1、激活、负向探针本轮均已活体复验）；CPU 推理成功路径（宿主 8GB 物理不可行，沿用设计秒拒结论）；在线底图 provider（无外网）。
- L-2 前端预构建镜像仍待负责人决策（沿 fix-plan 待办）。

## 6. 留痕索引

- 阶段一（静态套件+冒烟）：`D:/Temp/yunnan-test-1028/`（`backend_unittest_full.log` 首轮含失败明细、`backend_final_regression.log` 修复后终局、`miner_*.log`、`frontend_*.log`、`offline_deployment_test.log`、`ui_smoke.cjs`/`ui_smoke_result.log`、`geoview_login.cjs`、`shots/01..06*.png`）
- 阶段二（重型链路重放）：`D:/Temp/yunnan-test-1028/phase2/`（`e2e_inference_stress.log`、`cancel_publish*.log`、`cancel_immediate.log`、`quality_exports_and_revision.log`、`revision_409.log`、`snapshot_import.log`、`mysql_write_stress.log`、`cancel_visibility_repro.log`、`editor_smoke.log`、`shots/editor_111.png`；容器内 `/tmp/e2e_*.py` 一次性）
- 本轮代码改动：`backend/test_project_map.py`（仅测试缓存隔离，1 处 setUp，提交 `3b783b2`）；阶段二零代码改动（两项新发现未修，见 §4.1）
- 提交：`3b783b2`（测试隔离修复）+ `7a2bab7`（证据文档）+ 本阶段追加提交
