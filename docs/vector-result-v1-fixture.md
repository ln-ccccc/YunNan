# 分类成果矢量 v1 最小 fixture 说明

> 状态：测试样本包的冻结说明。本文只规定将来纳入测试目录的去敏样本、manifest 和断言，不声称这些二进制样本已经存在，也不引入模型权重、真实影像或大底图。

## 1. 目的与边界

此 fixture 用于让后端空间单测、API 契约测试和 GeoView mock 共享同一组小型输入/预期输出。所有 ID、像元值和坐标均为合成且可公开的测试数据；不得从真实项目、矿山、影像或用户资料复制内容。

该 fixture 验证 label GeoTIFF、ROI 掩膜、自动矢量化、16 像素碎斑阈值、可复现自动 `feature_id` 和 Revision 0 的基础语义。它不替代计划中针对不同输入/输出尺寸的 transform 专项单测，也不加载 MMSeg 权重或运行真实推理。

## 2. 预定目录与文件

以下路径是将来测试样本的唯一建议位置；当前任务只建立文档，不创建该目录或伪造二进制文件。

```text
tests/fixtures/classification-vector-v1/
├── fixture-manifest.json
├── input/
│   ├── crop-16x16-epsg4326.tif
│   └── prediction-mask-16x16.npy
├── roi/
│   └── mine-roi.geojson
├── expected/
│   ├── label-16x16-epsg4326.tif
│   └── auto.geojson
└── negative/
    ├── mask-without-reference-16x16.npy
    ├── invalid-class-save.json
    ├── invalid-geometry-save.json
    └── duplicate-feature-id-save.json
```

`prediction-mask-16x16.npy` 只是无空间参考的预测数组；它必须与 `crop-16x16-epsg4326.tif` 一起使用，才能生成空间正确的 label GeoTIFF。`mask-without-reference-16x16.npy` 用于证明单独 mask 不能发布成果。

## 3. 正向样本的固定空间定义

本正向样本固定为 `project_id=1`、`result_id=101`、`revision_no=0` 的计划中 `ready` 成果；`mine_fid`、`year` 和 `inference_job_id` 仅在 manifest 中以合成值记录。其 `auto_feature_collection` 与 `current_feature_collection` 必须相同且均为下文的 `expected/auto.geojson`，`current_revision_no=0`，`vector_error=null`。它不代表 `ready_empty` 或 `vector_failed` 的读取形状。

### 3.1 裁剪 GeoTIFF

`input/crop-16x16-epsg4326.tif` 必须是合成的三波段 `uint8` GeoTIFF，尺寸为 `16 × 16`，CRS 为 EPSG:4326。其像元值可以是固定的非敏感值，空间元数据必须严格如下：

| 属性 | 固定值 |
| --- | --- |
| raster width / height | `16 / 16` |
| band count / dtype | `3 / uint8` |
| transform | `Affine(0.01, 0, 0, 0, -0.01, 1.00)` |
| bounds | west `0.00`、south `0.84`、east `0.16`、north `1.00` |
| CRS | `EPSG:4326` |

该 transform 的第 0 行像元中心位于北侧，行号增加时纬度递减。它是纯合成坐标，不对应业务区域。

### 3.2 预测 mask 与矿山 ROI

`input/prediction-mask-16x16.npy` 是 `16 × 16` 的 `uint8` 类别数组。未列出的像元均为 `255`，非 NoData 像元按半开区间 `[start, end)` 定义：

| 类别 | rows | columns | 像元数 | 目的 |
| --- | --- | --- | --- | --- |
| `0` grassland | `[0, 4)` | `[0, 4)` | 16 | 恰好达到碎斑阈值，必须保留。 |
| `1` forest | `[0, 3)` | `[5, 10)` | 15 | 小于碎斑阈值，必须从自动 GeoJSON 丢弃。 |
| `2` building | `[8, 12)` | `[8, 12)` | 16 | 第二个保留类别。 |
| `3` road | `[12, 16)` | `[12, 16)` | 16 | 位于 ROI 外，生成 label 时必须改写为 `255`。 |

`roi/mine-roi.geojson` 必须是 EPSG:4326 的 `Polygon`，其环为：

```json
[
  [0.00, 1.00],
  [0.12, 1.00],
  [0.12, 0.88],
  [0.00, 0.88],
  [0.00, 1.00]
]
```

因此类别 0、1、2 位于 ROI 内，类别 3 只有边界接触而没有面积交集。该安排同时验证 ROI 外值写为 NoData，而不是在 GeoJSON 中产生道路要素。

### 3.3 期望 label GeoTIFF

`expected/label-16x16-epsg4326.tif` 是由预测数组与裁剪 GeoTIFF/ROI 生成的单波段 `uint8` GeoTIFF。它必须具备：

- `width=16`、`height=16`、`count=1`；
- `crs=EPSG:4326`，transform 与裁剪 GeoTIFF 完全相同，bounds 相同；
- `nodata=255`；
- 保留类别 0、1、2 的指定像元；类别 3 的 16 个像元和所有其他 ROI 外像元均为 `255`。

类别 1 虽然保留在 label GeoTIFF 中，但会在后续自动矢量化的 `<16` 像元过滤中消失。

### 3.4 期望自动 GeoJSON

`expected/auto.geojson` 必须是 EPSG:4326 的 `FeatureCollection`，Revision 0，且仅有两个要素。比较时应以几何等价、范围、类别和属性为准，不依赖 ring 起点或方向的文本顺序。

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "properties": {
        "feature_id": "auto-101-0-0001",
        "class_code": 0,
        "class_name": "grassland",
        "source": "auto",
        "result_id": 101,
        "revision_no": 0
      },
      "geometry": {
        "type": "Polygon",
        "coordinates": [[[0.00, 1.00], [0.04, 1.00], [0.04, 0.96], [0.00, 0.96], [0.00, 1.00]]]
      }
    },
    {
      "type": "Feature",
      "properties": {
        "feature_id": "auto-101-2-0001",
        "class_code": 2,
        "class_name": "building",
        "source": "auto",
        "result_id": 101,
        "revision_no": 0
      },
      "geometry": {
        "type": "Polygon",
        "coordinates": [[[0.08, 0.92], [0.12, 0.92], [0.12, 0.88], [0.08, 0.88], [0.08, 0.92]]]
      }
    }
  ]
}
```

自动 GeoJSON 中不得存在 class 1（15 像元碎斑）、class 3（ROI 外）或 `class_code=255` 的要素。

### 3.5 合法保存 payload

编辑器读取 `expected/auto.geojson` 后，保存前必须投影掉服务器权威属性。保存语义是完整当前 FeatureCollection 快照；下例是不修改两条自动要素的完整合法保存请求：

```json
{
  "base_revision_no": 0,
  "feature_collection": {
    "type": "FeatureCollection",
    "features": [
      {
        "type": "Feature",
        "properties": {
          "feature_id": "auto-101-0-0001",
          "class_code": 0
        },
        "geometry": {
          "type": "Polygon",
          "coordinates": [[[0.00, 1.00], [0.04, 1.00], [0.04, 0.96], [0.00, 0.96], [0.00, 1.00]]]
        }
      },
      {
        "type": "Feature",
        "properties": {
          "feature_id": "auto-101-2-0001",
          "class_code": 2
        },
        "geometry": {
          "type": "Polygon",
          "coordinates": [[[0.08, 0.92], [0.12, 0.92], [0.12, 0.88], [0.08, 0.88], [0.08, 0.92]]]
        }
      }
    ]
  }
}
```

该请求不得携带 `class_name`、`source`、`result_id`、`revision_no` 或其他业务属性；服务端返回的新 Revision 再补齐这些字段。`negative/duplicate-feature-id-save.json` 必须构造任一非空 ID（既有、临时或其他形式）重复出现的请求，并断言 HTTP 422、无修订/审计/当前版本改变。

### 3.6 与状态矩阵的对应

本 fixture 的正向路径只验证 `ready`：自动集合非空，当前集合等于 Revision 0 自动集合，`current_revision_no=0`。`ready_empty` 的实现测试必须另行构造“标签/ROI 有效但全部分量被过滤”的小输入，并断言自动集合与初始当前集合均为空、Revision 0 存在；该状态之后可以保存人工非空集合。

`negative/mask-without-reference-16x16.npy` 对应 `vector_failed`：结果和脱敏错误记录仍可读取，但 `auto_feature_collection=null`、`current_feature_collection=null`、`current_revision_no=null`，不得发布 label、自动 GeoJSON 或 Revision 0。对该结果的保存请求必须返回 HTTP `409`，并在 `data.details` 给出 `error="vector_failed"`、`vector_status="vector_failed"`、`save_allowed=false` 和脱敏原因；不能用空 FeatureCollection 伪装成功。

## 4. fixture manifest

`fixture-manifest.json` 必须为可机器读取的 JSON，并且为每个实际文件记录 SHA-256；哈希值不得为空、不得使用示例占位值。它至少包含以下字段：

| 字段 | 内容 |
| --- | --- |
| `fixture_version`、`fixture_id`、`sanitization` | v1 标识、稳定样本 ID，以及明确的 synthetic/de-identified 声明。 |
| `result_identity` | 测试专用的 `project_id=1`、`mine_fid`、`year`、`inference_job_id`、`result_id=101` 和 `revision_no=0`。 |
| `result_state` | 正向样本固定 `vector_status=ready`、`vector_error=null`、`auto_feature_count=2`、`current_feature_count=2`、`current_revision_no=0`。 |
| `crop` | 文件相对路径、SHA-256、宽高、波段数、dtype、CRS、transform、bounds。 |
| `prediction_mask` | 文件相对路径、SHA-256、宽高、dtype，以及类别块定义。 |
| `roi` | 文件相对路径、SHA-256、CRS、geometry type、bounds。 |
| `expected_label` | 文件相对路径、SHA-256、宽高、波段数、dtype、NoData、CRS、transform、bounds。 |
| `expected_auto_geojson` | 文件相对路径、SHA-256、CRS、FeatureCollection 类型、期望要素数、每个类别的要素数及固定 `feature_id` 列表。 |
| `vectorization_rules` | `minimum_component_pixels=16`、ROI 外写入 `255`、NoData 不产生要素。 |
| `negative_cases` | 每个负例文件、预期状态或 HTTP status，以及“不创建修订/审计或不发布成果”的断言；空间参考缺失负例还必须记录三个成果字段均为 `null` 与脱敏 `vector_error`。 |

manifest 中的相对路径必须都落在该 fixture 目录内；测试不得把 manifest 中的路径解释为服务器生产存储路径。

## 5. 必须执行的校验

| 校验层次 | 断言 |
| --- | --- |
| 完整性与去敏 | 每个 manifest 哈希匹配；无模型权重、真实影像、瓦片、镜像 tar、密钥或真实项目标识。 |
| 输入空间参考 | 裁剪 TIFF 可由 Rasterio 读取，CRS、transform 和 bounds 与 manifest 一致。 |
| label 写入 | 输出为单波段 `uint8`，NoData 为 `255`，CRS/bounds 与裁剪 TIFF 一致，ROI 外道路块为 `255`。 |
| 自动矢量化 | 按 Rasterio 4 邻域分量输出恰有 2 个要素；class 0 与 2 各 1 个，class 1 的 15 像元碎斑被移除，class 3 与 255 不生成要素。 |
| 属性与版本 | 两个要素均具备 v1 所需属性，`feature_id` 精确为 `auto-101-0-0001` 与 `auto-101-2-0001`，且均为 `source=auto`、`result_id=101`、`revision_no=0`。 |
| 正向状态 | `vector_status=ready`，自动集合与当前集合均有 2 个要素，`current_revision_no=0`，`vector_error=null`。 |
| GeoJSON 坐标 | 所有坐标为 EPSG:4326，几何等价于本文给出的两个矩形且位于 ROI 内。 |
| 负例：空间参考缺失 | 单独读取 `mask-without-reference-16x16.npy` 时必须得到 `vector_failed`；`auto_feature_collection`、`current_feature_collection` 与 `current_revision_no` 均为 `null`，不得发布 label、auto GeoJSON 或 Revision 0，既有 PNG 成果状态不得因此被改写。对其保存返回 HTTP 409，`data.details.error=vector_failed`，并含状态、`save_allowed=false` 与脱敏原因。 |
| 负例：非法保存 | `invalid-class-save.json` 含 `class_code=6` 或 `255`；`invalid-geometry-save.json` 含空、无效、非面、非有限/越界坐标或 ROI 外面。两者均返回 HTTP 422，且不创建修订、审计记录或当前版本更新。 |
| 负例：保存容量 | API 测试在运行时生成超过 10 MiB、超过 2,000 要素、单要素超过 5,000 顶点、总顶点超过 100,000 的四种请求；每种均返回 HTTP 422，且没有部分保存、修订、审计或当前版本改变。 |
| 负例：ID 归属 | 跨 `result_id`、不属于 `base_revision_no` 的既有 ID，以及非 `client-` 临时新 ID 均返回 HTTP 422；省略 ID 或 `client-` 临时 ID 保存后由服务端生成正式 ID。 |
| 并发保存 | 以过期 `base_revision_no` 再次保存时返回 HTTP 409，`data.details` 给出提交版本与当前版本，且不覆盖当前版本。 |

## 6. 使用约束

- 后端空间测试读取 GeoTIFF/`npy`，API 测试可读取 manifest 与 GeoJSON，前端 mock 只消费 manifest 中的 JSON 预期，不读取服务器私有路径。
- 固定样本不得被“更真实”的大数据替换；需要新增场景时，另建去敏 fixture 与 manifest，而不是修改本 v1 的金样本。
- 此 fixture 只证明小数据契约一致；真实模型、GPU、超大 TIFF、磁盘压力和完整离线部署仍按各自集成/发布验收执行。
