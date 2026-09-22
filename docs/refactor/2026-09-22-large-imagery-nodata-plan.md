# 超大影像推理 + 黑边掩膜方案 2026-09-22

## 需求

1. 解译平台需要可上传超大影像进行推理。
2. 大影像的黑边（无有效信息像元）应被 mask 掉，不参与推理产物。

## 现状核实（2026-09-22）

- 上传链路：单文件 8GB / 总量 8.5GB 硬上限已就位（tiff_processor.MAX_UPLOAD_TIFF_SIZE_MB + Werkzeug MAX_CONTENT_LENGTH + 前端 uploadGuards 同源预检）；>500MB 走 keep_tiff_raw 快路径、跳过 500MB 预览闸门（2026-09-19 迁移）。**上传侧无需新代码**。
- 推理链路（kml_roi）：按矿山 ROI 裁 bbox → 全分辨率裁剪 tif 落盘 → `read_tiff_as_rgb` **全量读入**再缩到 512×512 喂模型 → 产物直接落盘。两个问题：
  - **超大 ROI 内存/磁盘问题**：一个 50000×50000 的 bbox 会先写 ~7.5GB 裁剪 tif，再全量读入 RGB 数组——OOM 风险真实存在。
  - **黑边问题**：`read_tiff_as_rgb` 会把无效像元置白显示，但模型照样对白区分类，且 `draw_polygon_boundary_on_prediction` 保留**全量预测掩膜**（"Keep full-scene prediction mask"）——黑边区域以假类别颜色进入 mask/label.tif/变化矩阵/矢量。
- 下游 255 约定审计（全部已安全）：变化矩阵 `old < n` 排除 255；`compute_class_ratio_percent` `img < n` 排除 255；`write_label_geotiff` 以 255 为 nodata；`vectorize_label_geotiff` 只迭代 CLASS_DEFINITIONS（0-5），255 永不成面。
- 江西参考（B2，2026-09-19 验收反馈）：`max(RGB) ≤ 2` 判黑边 → 掩膜置 255、RGB 置黑；注释明示阈值对深色水体的边界风险（云南按同样阈值落地，黑边判定与展示解耦——模型看到的是置白背景，掩膜判定用原始像元）。

## 方案

### 1. 超大 ROI：裁剪上限 + 降采样读（raster_ops.crop_bbox_from_raster）

- `MAX_CROP_EDGE = 2048`：bbox 任一边超过 2048 时按长边比例 `out_shape` 降采样读窗口（rasterio 走 overviews/抽稀），transform 乘 `Affine.scale(w/out_w, h/out_h)` 保持地理参考正确。
- ≤2048 的常规 ROI **行为不变**（全分辨率裁剪）。模型输入本就是 512×512，>2048 的细节对产物无增益；label.tif/_src.png 的有效分辨率由 2048 封顶，内存与磁盘占用从 O(全图 bbox) 降为 O(2048²)。

### 2. 黑边掩膜：有效像元判定 + 三处落点

新增 `compute_tile_valid_mask(crop_tif, size)`（raster_ops）：按目标尺寸降采样读，无效 = 掩膜带 0 ∪ nodata 全波段命中 ∪ RGB 前 3 波段 max ≤ 2（江西 B2 阈值）。返回 bool 掩膜（True=有效）。

落点：
1. **prepare_tiles**（tiles.py）：裁剪后计算有效掩膜，以 `{src_base}_valid.png`（0/255）落 tile_dir；**有效占比 < 0.5% 的整块黑边 ROI 直接跳过该 variant**（不进推理队列——超大影像上纯黑边 ROI 零成本略过）。
2. **draw_polygon_boundary_on_prediction**（raster_ops.py）：新增可选 `valid_mask_path`——pred 图无效区置黑、预测掩膜无效区置 255（与 label.tif nodata 约定一致）；多边形描边在置黑之后绘制保持可见。
3. **distribute_outputs**（tiles.py）：传 valid 掩膜；写 label.tif 前把 `raw_labels[无效] = 255`——矢量生成、变化矩阵、占比统计经由既有 255 约定自动剔除。

### 3. 不改的东西

- `tif_to_png`/`read_tiff_as_rgb` 的置白展示策略不动（模型输入侧行为保持）。
- 上传上限 8GB 不动；前端零改动。
- `_write_masked_prediction_from_tile`（既有死代码）不动。

## 验证

1. 单测（新 test_kml_roi_nodata.py）：裁剪上限（大 bbox → ≤2048 且角点坐标经 transform 反算一致）；有效掩膜（nodata 命中/黑边/有效中心三类合成栅格）；整黑 ROI 跳过；distribute 级产物（mask 255 区、pred 黑区、label.tif 255 区）；常规小 ROI 行为不变（回归）。
2. 后端容器全量 pytest（基线 363）。
3. 真实栈 E2E：真实 tif 外垫黑边（rasterio 合成 padded 影像）→ 项目态推理 → 产物黑边区 mask=255/pred 黑/label 255。
4. GUI 绿后按本会话既定流程推送。

## 执行结果（2026-09-22 当日完成）

| 门 | 结果 |
| --- | --- |
| 单测 test_kml_roi_nodata.py | 7/7（首次 3 处断言笔误修正后全绿：窗口行数/自相矛盾断言/描边线覆盖边缘列均为测试预期错误，被测逻辑本身正确） |
| 后端容器全量 | **370 passed / 0 failed / 10 skipped / 149 subtests**（基线 363 + 新增 7） |
| 真实栈 E2E（API 全链路） | 见下 |
| GUI | 历史区渲染新产物（4 组：原图/预测结果/图例/编辑矢量成果）✓ |

### 真实栈 E2E 记录（项目 1 + 矿山 713，e2e_drill.tif 派生）

- 构造：矿山 713 在栅格中仅占列 155-175；将 153-166 涂黑（含 alpha 波段清零 + nodata=0）——矿山左半黑边、右半有效。
- 路由：project 模式匹配 [713, 714, 715] ✓；job `succeeded_with_fallback`（CPU）。
- 产物（713+2024）：mask unique=[1,5,**255**]，255 占 61.1%；**255 区置黑 96.6%**（余为多边形描边线穿过）；**左半 255 列均值 1.0、右半 0.223**（右半的 255 全是 ROI 外沿既有裁剪）——与构造精确一致。
- label.tif 255 占 74.3%（ROI 外沿 41.9% + 黑边）；class_ratio total=101,888 = 512²×38.86%（100%-61.14%）——有效像元剔除数学校验精确闭合。
- 附带发现（行为正确无需改码）：矿山完全落入黑边时，路由的 `_geometry_has_valid_pixels` 有效像元检查判 standalone 不建任务——全黑矿山天然不进推理。
- 上传链路复验：黑边影像经真实 /api/file/upload multipart 上传 → keepRawTiff 路径 → 项目态推理，全通。

### 遗留

- worker/后端容器已在验证前重启加载新代码；生产镜像重建时机由部署节奏决定（镜像内置代码不含本次改动前，靠主树挂载生效）。
