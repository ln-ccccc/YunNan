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
| 10 | 编辑器全功能 | 待执行 | |
| 11 | 保存 + 双开 409 | 待执行 | |
| 12 | 质检 | 待执行 | |
| 13 | 五格式导出逐个校验 | 待执行 | |
| 14 | 快照生成/恢复/跨环境导入 | 待执行 | |
| 15 | 归档/删除/恢复 | 待执行 | |

## 2. 性能实测

（按 docs/development-standard.md 固定数据与环境；目标值与实测分开）

| 项 | 目标 | 实测 | 证据 |
| --- | --- | --- | --- |
| A-1 GB 下载 >30s 不中断 | 限速客户端完整下载不中断、字节一致、BFF 存活 | ✅ 573M 原片（项目1 任务 55361cb4）经 BFF `mines/original-imagery/<jobId>/download` 限速 4MiB/s 下载 **143.4s** 完整收毕（旧 `AbortSignal.timeout(30000)` 此场景 30s 必断）：600,060,974B、md5 `1fd7139c…` 与服务端逐字节一致、Content-Length/attachment 头正确，下载后 BFF `/api/auth/session` 200 仍可服务 | 容器内 h2_download.py 输出 A1_REALSTACK_PASS |
| 分片上传 573M | 断点续传精确、合并不损坏 | ✅ 见步骤 5：10 片暂停→重 init 报 received=[0..9]→剩余 26 片 6.2s→合并 8.2s | bu0teg2x8 输出 |

待补：GB 上传（分片 vs 单发对照）、推理端到端、看板加载、地图连续进出 20 次、峰值 RAM/VRAM。

## 3. 韧性演练

待补：任务运行中 docker restart、kill worker、并发 409、慢网络下载中断恢复。

## 4. 真实数据发现（验收期间新查明）

| # | 发现 | 定性 | 证据 |
| --- | --- | --- | --- |
| R1 | **GPU 推理从未生效，且失败原因被误报**：宿主机 RAM 仅 5.93GB（Docker VM 5.79GiB），inference-worker 常驻 2.7GB（CPU 模型常驻内存）后，任务前 GPU 路径 `torch.load`（map_location='cpu'）1.97GB checkpoint 需 ~2× 宿主内存峰值 → `OSError: [Errno 12] Cannot allocate memory`。该异常不含 OOM/arch/mmcv 关键词，被 `_classify_gpu_error` 兜底归为 `GPU_INFERENCE_FAILED`，且 resolution 阶段丢弃原始异常不落日志 → 长期呈现为"GPU 能力检查失败"的误导性归因。项目 1 全部历史任务 succeeded_with_fallback 同因。手动复现：MMCV CUDA 算子 smoke 通过、GPU 可见性通过，仅模型加载败于宿主内存 | 环境约束 + 可观测性缺陷（代码） | gpu_repro.py 完整 traceback；`docker stats`/`free -m` |
| R2 | **worker 镜像缺 marshmallow → 成果发布审计链断裂**：发布管线 `publish_project_inference_result` → `from applications.project_hub.service import _append_activity` → service 顶层 import schemas → `from marshmallow import fields` → `ModuleNotFoundError`。有 try 兜底（成果不受影响），但 `inference_result_published` 活动记录全部丢失。`docker/requirements/inference-worker.txt` 无 marshmallow（backend/requirements.txt 有 3.18.0） | 部署缺陷（依赖清单） | worker 日志 WARNING + traceback |
| R3 | 项目 1 激活底图 tiles/2 全空（各 zoom 0 张 PNG）但任务记录 succeeded——历史成果瓦片被清理或丢失，地图实际无底图可渲染 | 存量数据问题（见步骤 3） | find 逐 zoom 计数 |

R1 建议（进 I-1 设计文档）：①设备探测/加载失败记录原始异常与分类码；②`Cannot allocate memory`/Errno 12 单独分类 `HOST_MEMORY_EXHAUSTED`，与 GPU 问题区分；③部署文档标注推理机 RAM 底线（1.97GB fp32 模型加载需 ≥8GB，推荐 16GB）。R2 修复：worker requirements 补 `marshmallow==3.18.0` 对齐 backend。

## 5. 结论与遗留

待验收完成后填写。
