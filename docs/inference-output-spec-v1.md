# 推理与输出规范 v1（Inference & Output Spec）

> 对应甲方建议《本期实施范围建议》第八节之②。描述当前推理任务与成果的**实际**状态机、产物与标识；变更须先改本规范（AGENTS §2.4）。输入侧约束见 [输入影像规范 v1](input-image-spec-v1.md)，矢量/编辑/版本规则见 [矢量成果契约](vector-result-v1-contract.md)。

## 1. 模型与类别表（固定，本期不在线训练）

- 模型：`mmseg:cc-ln/CUGRS`（DinoV3 + Swin，固定权重；推理优先 `model.inference.pth`，缺失回退训练权重并显式报错）。
- 类别表（标签值 = 类别代码）：

| 代码 | 类别 | 中文名 |
| --- | --- | --- |
| 0 | grassland | 草地 |
| 1 | forest | 林地 |
| 2 | building | 建筑 |
| 3 | road | 道路 |
| 4 | bareground | 裸地 |
| 5 | water | 水体 |
| 255 | — | NoData（ROI 外） |

## 2. 任务状态机（`inference/status.py`，非法转换直接拒绝）

```
queued ──► running ──► succeeded
   │            ├────► succeeded_with_fallback   （GPU OOM 回退 CPU 后成功）
   │            ├────► partial_failed            （部分图斑失败但有产出）
   │            ├────► failed
   └───────────►┴────► cancelled
```

- 终态集合：`succeeded / succeeded_with_fallback / partial_failed / failed / cancelled`。
- 取消语义：`queued` 立即置 `cancelled`；`running` 置 `cancel_requested` 由 worker 在图斑边界响应；对终态任务重复取消是幂等空操作。
- 公开序列化不泄露服务器物理路径（`_PRIVATE_PATH_FIELDS` 脱敏；越权访问他项目任务 404）。

## 3. 任务标识与可追溯字段

| 字段 | 来源 |
| --- | --- |
| `id`（UUID）、`status`、`error.code/message` | 任务表 |
| `project_id` + 绑定矿山 FID 列表 | 创建载荷（白名单字段校验） |
| 输入文件标识 | 载荷记录受控输入路径（数据集/投递相对路径）；**本期不记录源文件 SHA**（如需哈希指纹按增项评估） |
| `model_id` | 固定 `cc-ln/CUGRS`（发布时写入成果与数据集） |
| 设备与回退 | `requested/effective` 设备、`fallback_reason`（如 CUDA_OUT_OF_MEMORY）随任务返回 |
| 计时 | 逐阶段耗时记录于 `result_json`（台账导出消费） |

失败原因代码（非穷举）：`PAYLOAD_INVALID / WORKDIR_CONFLICT / JOB_TIMEOUT / GPU_INFERENCE_FAILED / INFERENCE_FAILED / WORKER_ISOLATED`。

## 4. 成果产物（按矿山 FID 发布，`projects/<pid>/outputs/inference/<fid>/`）

| 产物 | 形态 | 约束 |
| --- | --- | --- |
| 标签 GeoTIFF | `<fid>+<year>_label.tif`，单波段整数 | 带 CRS 与地理变换；ROI 外写 255；类别代码 ∈ {0..5,255}；发布前校验（非恒等/奇异变换拒绝） |
| 掩膜/预览 PNG | `<fid>+<year>_src.png` 等 | 仅浏览用，不作为正式矢量来源（甲方建议第四节） |
| 自动矢量 | 自动快照 FeatureCollection | 经 Polygonize、矿山边界裁剪、碎面过滤（最小面积阈值）、稳定要素 ID；`来源=自动`，不可被人工修订覆盖 |
| 统计文件 | `change_matrix_pixels.csv`、`change_matrix_percent_rownorm.csv`、`class_ratio_percent.json` | 变化矩阵与地类占比 |

发布原子性：暂存目录 → 逐 FID 原子替换（备份-替换-回滚）；任一 FID 失败整体回滚不留半发布状态。

## 5. 矢量化状态（成果级）

`ready`（已出矢量）/ `ready_empty`（矢量空：ROI 内无有效类别像元）/ `vector_failed`（自动矢量化失败，保留标签 GeoTIFF 与预览，允许人工直接修订）。

## 6. 验收口径

1. 任一终态任务的结果必须可由公开接口读取（脱敏后），失败任务给出上述错误代码之一与用户可读文案。
2. `succeeded*` 项目的成果资产在资产读模型中呈 `ready`（相对 storage key 解析；见 2026-09-20 审计修复）。
3. 标签 GeoTIFF 可被 GDAL 打开且 CRS/变换与源影像一致；ROI 外像元为 255。
4. 自动矢量发布后原始自动快照不可变；人工修订形成新 revision（`base_revision_no` 乐观锁，409 冲突不覆盖）——细则以 [矢量成果契约](vector-result-v1-contract.md) 为准。
