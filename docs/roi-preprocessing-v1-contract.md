# GeoView 矿山 ROI 预处理契约 v1

本契约补充项目化解译路由，不改变模型、类别、输入分辨率或原始影像。

## 请求与执行

`POST /api/analysis/kml_roi_inference` 支持可选整数 `prehandle`（0、2、4）和 `denoise`（0、3、5）。省略为 0；布尔值、字符串、空值及未知编号返回 400，不创建任务、不发布影像。普通 `semantic_segmentation` 使用同一编号校验。项目任务公开 `request` 在显式提交时返回这两个字段；历史任务缺字段按 0 执行。

顺序固定为增强后降噪：2 为逐通道 CLAHE（clipLimit=2、tileGridSize=8×8），4 为锐化核 `[[0,-1,0],[-1,5,-1],[0,-1,0]]`；3 为 3×3 中值滤波，5 为 3×3 高斯滤波（sigmaX=0、sigmaY=0）。复用已有 `image_processing` 算法，不增加额外 resize。

原流水线先生成 ROI 裁剪 TIFF 和 RGB PNG。只有显式非零选项才复制 RGB 到任务暂存目录 `preprocessed/`，应用上述算法后将同名 PNG 交给模型。原始 `tiles/` 中 TIFF、有效掩膜和 RGB 不改写，成果 `_src.png` 仍为原图；标签 GeoTIFF 继续从原始 crop 的 CRS 与 transform 生成。两个选项都为 0 时沿用原路径，不复制、不全文件算 SHA，模型输入逐字节相同。

## 可核对记录

显式处理的任务 `result.preprocessing` 包含 `prehandle`、`denoise`、`order`、`parameters`、`sources`、`tiles`。`sources` 记录源栅格角色和文件 SHA256；同一路径只读取一次。`tiles` 记录文件名、处理前后 RGB 数组 SHA256、原裁剪 TIFF 的 CRS、transform、宽高，以及模型 RGB 宽高。记录不包含服务器路径。任务日志记录同一摘要。空 ROI 返回选项与空 tiles，不声称已应用；坏 RGB/算法产物不可读或尺寸改变使任务失败，保留 worker 既有失败诊断行为。

成功、默认、无数据、参数错误和执行失败样例见 `tests/fixtures/roi-preprocessing-v1.json`。普通独立解译仍使用既有普通路径，不更改其缩放或成果格式。

## 本次实施与验证

主模块是推理运行时；输入是已有上传 TIFF、项目矿山范围和显式编号，输出是任务和暂存 RGB，消费者是 GeoView 与推理 worker。无部署变量、数据库表或正式目录结构变更。

1. 先用 API/任务/worker 测试证明选项被丢弃，用真实小 GeoTIFF 测试证明未应用。
2. 补统一校验、任务透传和流水线显式处理；前端请求使用提交时的选项快照。
3. 核对四种现有算法与组合顺序、原图/CRS/transform/掩膜不变、默认字节相同、失败不静默继续。
4. 在只读源码运行镜像中运行相关 unittest；运行 GeoView 测试与构建。真实 GPU 与浏览器由本轮集成验收分别记录，单测不代替真实验收。
