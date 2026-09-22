# 影像导入与分析能力增强：需求确立与执行计划 2026-09-22

## 0. 需求来源与决策记录

用户四块诉求：①扩充导入格式（IMG/多光谱/高光谱/DOM/DSM/常规 JPG、PNG）；②大体积底图导入优化（分批/断点续传/进度与剩余时长）；③底图裁剪与自定义切片（手绘范围+边界外扩+自动切片独立分析+自动关联项目）；④导入页面版面优化（格式说明/大小提示/参数面板/参数复用）。

**已确认四项边界决策**（2026-09-22）：
1. IMG/ENVI/DOM 等**直读不转换**（rasterio 容器内已验证 HFA/ENVI/JPEG/PNG 驱动全可用；原样落盘，不占双份磁盘）；
2. 自动切片用**实体切片**（写出 N 个真实 tif 并各注册为可独立分析数据集；选用才切，磁盘代价可控）；
3. **JPG/PNG 地理参考暂不做**（沿用既有非地理图片通道，不做世界文件/手输坐标）；
4. 裁剪与切片工具放 **Miner 影像管理页**（项目上下文、数据集登记、空间工具都在这一侧）。

## 1. 现状 vs 需求 Gap 矩阵

| 需求 | 现状 | 缺口 |
| --- | --- | --- |
| 多格式导入 | `is_tiff_file` 仅放行 .tif/.tiff（tiff_processor.py:56）；下游 `read_tiff_as_rgb`/`tif_to_png`/`resolve_uploaded_tiff`、推理 `prepare_tiles` 全链以 tif 为前提；UploadSet 扩展名含 jpg/png/gif 但仅走非地理遗留通道 | 格式探测与放行、统一读取入口、ENVI 头文件伴生成对、推理输入解析泛化 |
| 分批导入/断点续传 | **分片续传已落地**（100GB 通道：init/chunk/complete，64MB 块、取消、断点续传、进度百分比——2026-09-22 当日交付） | 剩余时长（ETA）与已传/总量的实时展示；通道对新格式无感知差异（传输层本就格式无关，需前端阈值放开通用） |
| 范围裁剪 | 推理时按矿山 KML ROI 窗口裁剪已存在（`crop_bbox_from_raster`），但产物只进推理流程，**无独立裁剪工具/产物登记** | 裁剪端点（多边形+外扩）、裁剪产物登记为项目影像、前端范围绘制 |
| 自动切片 | 无 | 切片端点（固定像素/固定面积网格）、批量数据集登记、切片清单与单片区分析入口 |
| 导入页面版面 | 上传卡仅 tif 提示；有本地预检秒拒 | 格式适配说明、大小上限提示强化、裁剪/切片参数面板与参数预设复用 |

## 2. 分期执行计划（S1→S4，每期独立交付可验证）

### S1 格式扩展（直读）

**后端**（契约先行，fixture 先写）：
- 新模块 `applications/common/utils/raster_formats.py`：
  - `SUPPORTED_RASTER_EXTENSIONS = {'tif','tiff','img','jp2'}`；`ENVI_DATA_EXTENSIONS = {'dat','bin','img'…}` 与 `.hdr` 伴生规则（ENVI 数据文件必须与同名 .hdr 同批存在）；
  - `detect_raster_kind(filenames)` → `{kind: 'geotiff'|'erdas_img'|'envi'|'jp2', data_file, header_file?}`；
  - `validate_raster(path)`：rasterio.open（驱动自动识别）+ CRS 存在 + 波段数>0；IMG/JP2 单文件即可，ENVI 需成对。
- 泛化调用点：`is_tiff_file` → `is_supported_raster`（保留旧名别名，调用点逐个换：upload_one 快速路径、analysis 推理闸门、`resolve_uploaded_tiff`→`resolve_uploaded_raster`、tiles 链 `tif_to_png`→`raster_to_png`、`read_tiff_as_rgb`→`read_raster_as_rgb`——内部仍走 rasterio，驱动自动识别 IMG/ENVI，零转换）。
- 上传批次约束：`.dat/.bin` 无同名 `.hdr` 时拒绝（400，明确提示"ENVI 影像需连同 .hdr 头文件一起上传"）；`.hdr` 单独上传同样拒绝。UploadSet 扩展名补 `img/dat/bin/hdr/jp2`。
- DOM/DSM：主流交付即 GeoTIFF/IMG，随格式扩展自然支持，文档明示。
**前端**：TiffUploadCard 接受列表与 `isValidImagery`（与后端同源常量）、拖拽提示文案更新；分片阈值对全部影像格式生效。
**验证**：fixture 用 rasterio `driver='HFA'/'ENVI'` 现造 IMG/ENVI 样例（容器内驱动支持创建），覆盖上传→校验→推理全链；JP2 若容器 openjpeg 不可创建则仅保留扩展名并在文档标注。

### S2 大文件导入体验增强（小期）

- 进度体升级：`已上传 43% · 2.4GB/5.6GB · 预计剩余 03:12`——滑动窗口速率估算 ETA（纯函数模块 + 测试：速率稳定/突变速/总量未知三态）；分片通道与新格式打通（阈值判断改为格式无关）。
- 暂停/续传语义显性化：取消按钮文案改"暂停（可续传）"，续传时进度直接跳至已收分块（后端 done/received 已支持，纯前端呈现）。
**验证**：ETA 纯函数测试 + GUI 大文件实测（续传跳进度可见）。

### S3 裁剪与切片（最大期，Miner 影像管理页）

**后端**（新模块 `applications/project_hub/imagery_processing.py`）：
- `GET /api/projects/:id/imagery/candidates`：可操作影像清单（数据集 kind=imagery + interpretation 输入），带分辨率/尺寸/波段/大小摘要。
- `POST /api/projects/:id/imagery/clip`：`{source_key, geometry(GeoJSON 4326) | mine_fid, buffer_meters}`——rasterio 按多边形窗口（外扩换算像素）裁剪写出 tif 至 `projects/<id>/inputs/imagery/`，登记 ProjectDataset（display_name 带裁剪标记与来源）。
- `POST /api/projects/:id/imagery/slice`：`{source_key, mode: 'grid_pixels'|'grid_area', tile_pixels | tile_area_m2, buffer_pixels=0, max_tiles=64}`——按 transform 计算网格；过小边片丢弃并报告；逐片写出+批量登记数据集（单事务，部分失败回滚整体）；返回切片清单（尺寸/路径/数据集 id）。
**前端**（ImageryView 升级为工作台）：
- 影像候选表（格式/尺寸/波段/大小）；
- 裁剪工具：内嵌 Leaflet 小地图（项目矿山边界为参照），两种范围来源——**地图手绘多边形**（点击加顶点、双击闭合，自研轻量交互不引绘制库）或**选矿山自动取其边界**；外扩距离输入（米，默认 0，边界外扩防漏判）；
- 切片工具：参数面板（固定像素 / 固定面积二选一 + 上限片数）；执行后切片清单，每片提供"去解译"入口（跳 GeoView 带 project_id）；
- 参数预设：常用裁剪外扩/切片规格存 localStorage 一键复用。
**验证**：合成大栅格（如 20000×20000）切片数学校验（网格数/边片丢弃/总量上限）；裁剪地理参考角点反算；数据集登记与清单回读；GUI 全流程（手绘→裁剪→清单；参数→切片→去解译）。

### S4 导入页版面与文档（收尾小期）

- GeoView 上传卡：格式适配说明区（支持格式清单+ENVI 成对要求+需带 CRS）、大小上限提示（预检已有，补展示位）；Miner 裁剪/切片参数面板定稿（S3 已含预设）。
- 文档：格式支持矩阵（输入影像规范文档更新）+ 计划文档执行结果回填。

## 3. 边界与不做（本期）

- JPG/PNG 地理参考（世界文件/手输坐标）——决策暂不做，沿用非地理遗留通道；
- 高光谱专项处理（波段选择器/光谱库匹配）——多波段直读即支持，专项分析另立；
- 虚拟窗口切片——已决策实体切片；
- GeoView 侧裁剪入口——工具归 Miner，GeoView 仅作解译承接方。

## 4. 执行纪律

沿用本会话已验证流程：每期契约/fixture 先行→提供方→消费方→集成验证；三套回归门（容器 pytest / node --test / 构建）+ 真实栈 live + GUI 实测 + ocr 规则审查新增文件；P0/P1 当期修。

## 5. 执行结果（S1-S4 全部交付，2026-09-22 当日）

| 期 | 提交 | 核心交付 | 验证 |
| --- | --- | --- | --- |
| S1 格式扩展 | cad9b5a | raster_formats 模块（IMG/ENVI/JP2 直读、ENVI 成对校验、共享词干落盘）、全链泛化 | backend 726/0（新 9 例）+ GUI IMG 全链实测 |
| S2 导入体验 | c2d25de | uploadProgress 纯函数（滑动窗口 ETA）、进度文案"42% · 2.4GB/5.6GB · 预计剩余"、暂停（可续传）按钮 | frontend 47/47 + build |
| S3 裁剪切片 | c28e4cb | imagery_processing 三端点（candidates/clip/slice）、SVG 手绘多边形、切片参数预设 | backend 885/0（新 11 例）+ live（572MB 切 6 片/矿山裁剪）+ GUI |
| S4 版面文档 | 本提交 | 上传卡格式适配说明折叠区、计划文档回填 | build + 测试全绿 |

最终基线：backend **885/0**、frontend **47/47**、miner **64/64**。
