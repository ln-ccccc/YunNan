# 输入影像规范 v1（Input Image Spec）

> 对应甲方建议《本期实施范围建议》第八节"四份接口约定"之①。本规范描述**当前实现实际接受与拒绝的输入**，不是愿景文档；实现变更须先改本规范再改代码（AGENTS §2.4）。

## 1. 适用范围

- 底图（basemap）：走**部署端目录投递**链路（`project_storage/incoming`），服务端切片，PNG/XYZ 仅用于浏览。
- 推理输入（影像数据集）：走**登记引用**（项目数据集 `file_path` 指向受控相对路径）或 GeoView 上传（`/_uploads`）。
- 本期仅支持 **TIF/TIFF**。IMG 等其他格式未做样本适配与精度验证，不在承诺范围（如需支持按增项走适配+验证流程）。

## 2. 格式与地理要求

| 项 | 要求 | 实现依据 |
| --- | --- | --- |
| 扩展名 | `.tif` / `.tiff`（候选登记仅认这两个后缀） | `spatial_service._candidate_relative_path` |
| CRS | **必须有**；缺失即拒绝（"GeoTIFF 缺少 CRS"） | `spatial_service._raster_metadata` |
| 地理变换 | 必须有效（仿射变换），标签写出前二次校验 | `raster_ops.has_valid_geotransform` |
| 范围 | 底图范围必须与项目**激活矿山范围**相交，否则拒绝登记 | `register_basemap` |
| NoData | 原样保留；标签 GeoTIFF 的 ROI 外像元写 255 | `raster_ops.write_label_geotiff` |
| 波段/位深 | 模型推理按原始 GeoTIFF 窗口裁剪读取（`keepRawTiff` 快速路径），不做有损的 3 波段 uint8 预转换；切片预览路径才会生成 PNG | `upload.process_uploaded_tiff`、推理 worker |

## 3. 大小限制

| 链路 | 上限 | 行为 |
| --- | --- | --- |
| 浏览器上传（`/api/file/upload`） | 硬上限 **8GB**（`MAX_UPLOAD_TIFF_SIZE_MB=8192`，HTTP 层与视图层同源） | 超限 413/业务失败 |
| 切片预览/整图读内存预处理 | **500MB**（`MAX_TIFF_SIZE_MB`） | 超限优雅降级：保留原始文件，不拒绝上传 |
| 服务端目录投递 | 无代码上限；登记时做**磁盘预检**（可用空间 ≥ max(2×源文件, 100MB)） | 不足即拒绝登记 |

## 4. 目录投递约定（半文件防护，甲方建议 2026-09-03）

原始底图通过部署端进入 `project_storage/incoming/`（可子目录）。投递工具**必须**遵守：

1. **复制期间**使用标记段命名：`<名称>.staging.tif`、`<名称>.part`、`<名称>.tmp`、`<名称>.crdownload`。
   系统行为：候选扫描**跳过**这些文件（不出现在列表）；直接登记会被拒绝（"底图文件仍在复制中"）。
2. **复制完成后**改回正式名（建议 `os.replace` 原子改名）。可选写 `<名称>.tif.ready` 完成标记（系统不强制，当前不消费该标记；incoming 侧的 `.ready` 仅供投递工具自证）。
3. 服务端把源文件复制进项目目录时（`basemaps/<resource_id>/`）使用**临时文件+原子替换**，并在完成后写 `<名称>.tif.ready`（内容为字节数）；续跑时按 `.ready` 或字节数一致性判定是否重复制——中断的半文件会被完整副本替换。

## 5. 命名建议

- 投递文件名使用可读名称（如 `<县区>_<年份>.tif`）；正式名内**不要**包含 `staging/part/tmp/crdownload` 独立段（会被当成半文件跳过）。
- 同名辅助文件（`.ovr/.tfw/.prj/.enp/.aux.xml`）随主文件一起投递，登记/复制时自动携带。

## 6. 拒绝态一览（登记阶段）

| 状态 | 文案（节选） |
| --- | --- |
| 非 incoming 目录 | 底图必须来自 project_storage/incoming 目录 |
| 非 TIF/TIFF | 底图仅支持 TIF/TIFF |
| 半文件命名 | 底图文件仍在复制中（.staging/.part） |
| 无 CRS / 读不出 | GeoTIFF 缺少 CRS / 无法读取 GeoTIFF |
| 与矿山范围不相交 | 底图范围与项目矿山范围完全不相交 |
| 磁盘不足 | 磁盘空间不足，至少需要 N 字节可用空间 |

验收口径：按本规范投递的合格输入必须可被扫描、登记、切片并在完成后原子启用；不合格输入逐项得到上表对应业务错误，而不是 500 或列表整体失败。
