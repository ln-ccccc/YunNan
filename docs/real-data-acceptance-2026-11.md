# 真实大数据全功能验收记录（H-2）

日期：2026-10-25 起（进行中）。执行环境：本机 Docker 栈 + GPU 推理 Worker。
对应计划：docs/fix-plan-20261024.md 批次 H-2；上游审查：docs/code-review-2026-10-24.md。

> 状态：本文档随验收推进逐节填写。每步记录操作、证据（ID/版本/来源一致性）与实测耗时。

## 0. 环境基线

| 项 | 值 | 证据 |
| --- | --- | --- |
| 代码基线 | main @ ffbd82d（批次 A–G 修复 + F 影像上手绘全部入库） | `git log --oneline -1` |
| 容器 | backend / frontend / miner-api / miner-web / inference-worker / spatial-worker / mysql 七容器 | `docker ps` |
| GPU | NVIDIA RTX 5060 8GB，驱动 581.15，CUDA 12.8 镜像 | `nvidia-smi` |
| 存储规模 | project_storage 共 14G（projects） | `du -sh` |
| 真实数据 | 大理白族自治州底图 11G TIFF（Level_15）+1.3G ovr；2024 推理影像含 573M TIFF；矿山 KML yunnan.kml 3.2M（真实 FID） | `ls -lh maps/dali`、`projects/1/inputs/interpretation/2024` |
| 既有真实历史 | 项目 1「云南矿山生态修复监测项目」active：推理任务 succeeded/succeeded_with_fallback 多条（2026-09），成果 2024 年 fid 713–715 vector_status=ready | MySQL 只读查询 |

栈重启记录：容器已运行 42h（早于 2026-10-24 修复提交），验收前重启 backend/两 worker/miner-api/miner-web/frontend 以加载当前代码（bind mount 源码 + 前端容器入口重建 dist）。

## 1. 全流程 E2E（进行中）

验收项目：项目 7「real-data-acceptance-2026-11」（独立新建，不复用项目 1 存量数据；原始数据只读共享）。步骤与证据：

| # | 步骤 | 状态 | 证据/耗时 |
| --- | --- | --- | --- |
| 1 | 建档 | ✅ 2026-10-25 | API 创建 project_id=7，active，monitor 2024；missing_resources=[mine_vector, basemap] 符合初始预期 |
| 2 | 真实 KML 导入/字段映射 | ✅ 2026-10-25 | yunnan.kml（3.2M，565 个真实矿山图斑）：preview 返回 72 字段、suggested mapping fid=FID_1（validation=valid）、bounds=[99.06,24.81,100.87,26.64]；import 成功建 mine_vector 资源 |
| 3 | 底图登记/切片/激活 | ✅ 2026-10-25（retry 后成） | 登记大理底图 10.7G（50777×52620，Pseudo-Mercator，bounds 与矿山相交=true），resource_id=14，zoom 8–15，job f8a60224。首轮 13%（~2045 片）后 failed：`TIFFReadEncodedTile failed at X offset 113, Y offset 30`（GDAL 块读错误）。排查：坏块在原 TIFF 与 `projects/7/basemaps/14/` 副本中此刻均可正常读出（md5 均 d41d8cd98f=空字节），指向 Docker Desktop 宿主挂载（D: 经 /run/desktop/mnt/host）高负载下瞬态 I/O 而非数据损坏。**retry 接口重切：progress 从 13.1% 续起，1442s 后 succeeded**——同一块这次顺利读出，瞬态假说证实。切片成功后自动激活（spatial_worker 置 status=active + .active 标记）：瓦片 3.9G，zoom8=4 / zoom11=126 / zoom15=24086 张 PNG。附带真实数据发现：项目 1 激活底图 `tiles/2` 各 zoom 目录 0 张 PNG，但其 2026-07-19 切片任务记录为 succeeded——该底图实际已无瓦片可渲染 |
| 4 | 影像登记 | ✅ 2026-10-25 | 真实 573M TIFF（项目1 2024 推理影像副本，20000×10000，EPSG:3857）经 ops 放入 incoming/ + `POST /datasets` 登记 dataset_id=43（脚本重跑产生重复登记 44——登记接口非幂等，已记录，无删除 API） |
| 5 | GB 级分片上传（暂停/续传） | ✅ 2026-10-25 | 573M 真实 TIFF（600,060,974B）×16MiB×36 片：传 10 片后模拟关闭浏览器 → 同 upload_key 重新 init **幂等复用会话并精确报告 received=[0..9]** → 剩余 26 片 6.2s → complete 合并 8.2s，photo_id=19（isSlice+keepRawTiff）。注：中途一次断言失败为演练脚本读错响应键（received_chunks vs 实际 received），服务端行为正确，修正后全过 |
| 6 | 影像上手绘裁剪（P1-8 新功能真实验证） | ✅ 2026-10-25 | 预览接口 dataset_id=43：12.8s 出 2048×1024 PNG（~7.1MB base64），真实 bounds=[100.507,25.940,101.365,26.326]；按真实矿山 fid696 开采中心（KFX=100.784167/KFY=26.043879，KML 属性）构造手绘多边形（±0.008°/±0.006°+50m 外扩）→ clip 2.8s 出 dataset_id=45（394×332），**storage_key=相对键 `projects/7/inputs/imagery/clip_20261024_172540_3d159b2d.tif`（B 批次契约在真实栈生效）** |
| 7 | 切片 | ✅ 2026-10-25 | grid_pixels 512 → 1 片（394×332 不足两片）：dataset_id=46，相对键 slice_20261024_172541_063a8a_r0c0.tif |
| 8 | GPU 推理 | ✅ 2026-10-25（succeeded_with_fallback） | `POST /api/inference/jobs` {project 7, dataset 46（**上手绘 clip→slice 产物**）, year 2024, mine_fids [696]}——**P1-1 契约修复的关键验证达成：clip 产物被推理端完整接受**。任务 b9af125f：86s（17:56:03→17:57:29），1/1 tile，effective_device=cpu，fallback_reason=GPU_INFERENCE_FAILED。GPU 失败根因已查明（见 §4 发现 R1）：宿主 RAM 5.93GB 不足以 torch.load 1.97GB checkpoint（需 ~2× 峰值）→ `OSError: [Errno 12] Cannot allocate memory` 被误分类为 GPU_INFERENCE_FAILED |
| 9 | 矢量化 | ✅ 2026-10-25（发布管线自动完成） | 成果 id=62：fid 696/2024，vector_status=ready，feature_count=146，current_revision_no=0，label_path 与 auto_geojson_path 均落盘；auto_feature_collection 首要素坐标 (100.78387, 26.04445) 与矿山 KML fid696 开采中心吻合 |
| 10 | 编辑器全功能 | 🔄 归 H-3 | 改类/顶点/增删/undo/合并/simplify 属浏览器交互，随 H-3 GUI 全量验收复跑（4 冒烟 + 编辑器操作流）；本节先以 API 完成保存/冲突/质检/导出链 |
| 11 | 保存 + 双开 409 | ✅ 2026-10-25 | 双会话（独立 cookie）：A 保存自动基线 146 要素成功（rev 0→1，**R4 修复后**；修复前严格 covers 拒 40/146 要素，必 422）；B 持旧 base 再存 409"成果已被其他保存更新"（detail 含 base/current revision）；B 刷新 current 后重试成功；revisions 列表 0..6 连续完整（本轮累计 6 次 manual 保存） |
| 12 | 质检 | ✅ 2026-10-25 | `GET quality-review?small_area_threshold_m2=200`：146 要素 5 类——water 15 块 58.2%（5422.6m²）、grassland 41 块 25.0%、…（真实面积/占比统计） |
| 13 | 五格式导出逐个校验 | ✅ 2026-10-25 | geojson 1745KiB（JSON 可解析）/ csv 23.9KiB（表头+行）/ shp 545KiB（zip PK 头）/ xlsx 6.0KiB（zip PK 头）/ dxf 3284KiB（SECTION 头），五格式全部 200 且 artifact 下载 Content-Disposition 正确；导出目录 exports/<id>/artifact 落盘 |
| 14 | 快照生成/恢复/跨环境导入 | ✅ 2026-10-25 | 生成 backup id=4/5（0.11s，completed+restorable）；manifest 下载（`snapshot-5-manifest.json`）校验：565 矿山/5 数据集/5 导出/14 活动，**dataset 键全为相对键且无绝对路径泄漏**（含 `incoming/real_dali_2024_573m.tif`×2、`projects/7/inputs/imagery/clip|slice_*.tif`、`projects/7/outputs/inference/696`）。同环境恢复 0.36s：矿山/数据集/导出原量恢复，时间线保留原始时间戳（project_created 仍在）+ backup_restored 新增，成果表不受影响（62/63 仍 ready）。**跨环境导入（B-5 真实验证）**：新建项目 8 → `POST backups/import` 0.34s → DB 断言 4 个 imagery 数据集全部重定位 `projects/8/inputs/imagery/`（**incoming/ 键 → `projects/8/inputs/imagery/real_dali_2024_573m.tif`**；`projects/7/inputs/imagery/clip_*.tif` → projects/8 前缀改写），inference_result 类按 projects/ 分支整子树重定位 `projects/8/outputs/inference/696`，矿山 565 全量绑定，imports/ 存档 manifest 落盘，project_imported 审计含源项目/快照 id；负例：绝对路径 `/etc/passwd` 数据集导入 422 拒绝。导入目标项目 8 验毕即清理（archive→delete→trash） |
| 15 | 归档/删除/恢复 | ✅ 2026-10-25（含一次真实删除+人工恢复演练） | 负例：活跃态 DELETE → 409"仅已归档项目可以删除"；archive 0.03s → summary.lifecycle_status=archived、列表带 archived 标记。**真实删除演练（意外触发，全链走通）**：归档态下 DELETE → 200 `trash_target=7_20261024_182501_6361b02f`，目录整体入 trash、DB 软删（deleted_at 置位、关联行全保留）、project_deleted 审计落时间线；列表/详情立即不可见。**人工恢复**（trash 恢复无 API，按部署文档手工）：`mv trash/7_* projects/7` + `UPDATE project SET deleted_at=NULL` + `POST /api/projects/7/restore` → active，**数据零丢失**：565 矿山、5 数据集、成果 62/63 ready、影像候选 6、底图瓦片 32503 张 PNG（`tiles/14/`，3.9G）、时间线 deleted+restored 完整可溯。设计观察（进 I-1）：trash 恢复纯手工（mv+SQL），无受控 API/CLI；项目详情端点业务失败返回 HTTP 200+`success:false`（既有 business_or_server_failure 惯例，REST 语义偏弱） |

## 2. 性能实测

（按 docs/development-standard.md 固定数据与环境；目标值与实测分开）

| 项 | 目标 | 实测 | 证据 |
| --- | --- | --- | --- |
| A-1 GB 下载 >30s 不中断 | 限速客户端完整下载不中断、字节一致、BFF 存活 | ✅ 573M 原片（项目1 任务 55361cb4）经 BFF `mines/original-imagery/<jobId>/download` 限速 4MiB/s 下载 **143.4s** 完整收毕（旧 `AbortSignal.timeout(30000)` 此场景 30s 必断）：600,060,974B、md5 `1fd7139c…` 与服务端逐字节一致、Content-Length/attachment 头正确，下载后 BFF `/api/auth/session` 200 仍可服务 | 容器内 h2_download.py 输出 A1_REALSTACK_PASS |
| 分片上传 573M | 断点续传精确、合并不损坏 | ✅ 见步骤 5：10 片暂停→重 init 报 received=[0..9]→剩余 26 片 6.2s→合并 8.2s | bu0teg2x8 输出 |
| 快照链 | 生成/恢复/导入可用 | ✅ 生成 0.11s、同环境恢复 0.36s、跨环境导入 0.34s（见步骤 14） | h2_snapshot.py / h2_snapshot_db.py |

待补：GB 上传（分片 vs 单发对照）、推理端到端、看板加载、地图连续进出 20 次、峰值 RAM/VRAM。

## 3. 韧性演练

待补：任务运行中 docker restart、kill worker、并发 409、慢网络下载中断恢复。

## 4. 真实数据发现（验收期间新查明）

| # | 发现 | 定性 | 证据 |
| --- | --- | --- | --- |
| R1 | **GPU 推理从未生效，且失败原因被误报**：宿主机 RAM 仅 5.93GB（Docker VM 5.79GiB），inference-worker 常驻 2.7GB（CPU 模型常驻内存）后，任务前 GPU 路径 `torch.load`（map_location='cpu'）1.97GB checkpoint 需 ~2× 宿主内存峰值 → `OSError: [Errno 12] Cannot allocate memory`。该异常不含 OOM/arch/mmcv 关键词，被 `_classify_gpu_error` 兜底归为 `GPU_INFERENCE_FAILED`，且 resolution 阶段丢弃原始异常不落日志 → 长期呈现为"GPU 能力检查失败"的误导性归因。项目 1 全部历史任务 succeeded_with_fallback 同因。手动复现：MMCV CUDA 算子 smoke 通过、GPU 可见性通过，仅模型加载败于宿主内存。**恶性循环确认**：CPU 回退后 CPU 模型常驻挤占内存，后续任务 GPU 尝试永远失败——重启 worker（清空常驻）后下一任务 80034f77 **真正跑在 cuda:0**（32s，较 CPU 86s 提速 2.7×），审计 inference_result_published 同时落库（R2 修复生效） | 环境约束 + 可观测性缺陷（代码） | gpu_repro.py 完整 traceback；`docker stats`/`free -m`；任务 80034f77 终态 |
| R2 | **worker 镜像缺 marshmallow → 成果发布审计链断裂**：发布管线 `publish_project_inference_result` → `from applications.project_hub.service import _append_activity` → service 顶层 import schemas → `from marshmallow import fields` → `ModuleNotFoundError`。有 try 兜底（成果不受影响），但 `inference_result_published` 活动记录全部丢失。`docker/requirements/inference-worker.txt` 无 marshmallow（backend/requirements.txt 有 3.18.0） | 部署缺陷（依赖清单） | worker 日志 WARNING + traceback |
| R3 | 项目 1 激活底图 tiles/2 全空（各 zoom 0 张 PNG）但任务记录 succeeded——历史成果瓦片被清理或丢失，地图实际无底图可渲染 | 存量数据问题（见步骤 3） | find 逐 zoom 计数 |
| R4 | **自动矢量化产物无法通过自身保存校验（人工编辑保存链从未走通）**：保存校验 `roi.covers(geometry)` 无容差，矢量化坐标与 ROI 边界浮点相切（~1e-9 度）被拒——真实成果 62 共 146 要素中 40 个越界丝米级，GeoView 打开后任何保存必 422"几何必须完全位于成果 ROI 内"。佐证：classification_revisions 全表 source=auto（rev 0），**无任何 manual 修订**。修复（已入库）：covers 加 1e-9 度（亚毫米）容差，只吸收浮点误差，像素级真实越界仍拒；回归用例（ROI 边界相切 sliver 可存 / 大越界仍拒） | 代码缺陷（P1 级数据链断裂） | 诊断脚本 40/146 计数+buffer(1e-9) 全通过；修复后真实栈 146 要素保存成功 rev0→6 |

R1 建议（进 I-1 设计文档）：①设备探测/加载失败记录原始异常与分类码；②`Cannot allocate memory`/Errno 12 单独分类 `HOST_MEMORY_EXHAUSTED`，与 GPU 问题区分；③CPU 回退后在空闲期卸载 CPU 模型重试 GPU，打破"回退一次永远 CPU"的恶性循环；④部署文档标注推理机 RAM 底线（1.97GB fp32 模型加载需 ≥8GB，推荐 16GB）。R2 修复（已入库 8ef0c93）：审计 import 直连 `_foundation`，不再依赖 worker 镜像补装 schemas/osgeo 依赖链。

## 5. 结论与遗留

待验收完成后填写。
