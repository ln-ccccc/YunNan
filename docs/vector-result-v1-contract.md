# 分类成果矢量 v1 契约

> 状态：v1 数据与 HTTP 契约。2026-09-05 在项目工作台开发分支核对到成果路由、修订服务、GeoView 编辑器及对应测试代码；这不代表已合并、已部署或已完成实机验收。接手时以目标工作树的代码与测试结果确认实现状态。

## 1. 适用范围与变更规则

本契约适用于项目内的自动分类成果、GeoView 读取与编辑、服务端 GeoJSON 导出，以及受会话与项目归属校验保护的底图瓦片读取。v1 的破坏性变化必须新建 v2，不能静默重命名字段、类别代码、路由或错误语义。

除文件下载外，JSON 响应沿用项目既有的响应信封；本契约不重定义既有顶层字段。下文出现的返回字段均位于既有响应的 `data` 中。错误细节必须位于 `data.details`，且前端 Axios 封装不得吞掉 HTTP status。

`{project_id}` 和 `{result_id}` 是正安全整数，必须按现有项目主键约束解析；v1 不接受 UUID、任意字符串、服务器路径或任意 URL 作为这两个标识。`{resource_id}`、`{z}`、`{x}`、`{y}` 仅表示路由参数，不能用于传入服务器路径或任意 URL。

## 2. 空间、标签与类别

### 2.1 坐标系与 GeoJSON

- 所有 API 中返回的几何和所有 v1 GeoJSON 导出均为 EPSG:4326，坐标顺序为 GeoJSON 的二维 `[longitude, latitude]`。每个 position 必须恰有两个数值元素；拒绝第三维、更多维度、非数组 position 与非有限数值。
- 中间及可复核的 label GeoTIFF 不强制重投影到 EPSG:4326；它必须保留原裁剪 TIFF 的 CRS 与空间范围。
- 分类面只接受并返回 `Polygon` 或 `MultiPolygon`。自动面在写入 GeoJSON 前重投影到 EPSG:4326，并裁切到对应矿山 ROI。

### 2.2 固定类别表

以下类别代码、英文名称、中文名称和 RGB 调色板来自现有 `mmseg_segmentation.py` 的 `CLASS_NAMES` 与 `PALETTE`，在 v1 中不得变化。

| class_code | class_name | 中文名称 | RGB |
| --- | --- | --- | --- |
| 0 | grassland | 草地 | `[0, 255, 0]` |
| 1 | forest | 林地 | `[0, 128, 0]` |
| 2 | building | 建筑 | `[255, 0, 0]` |
| 3 | road | 道路 | `[255, 255, 0]` |
| 4 | bareground | 裸地 | `[255, 0, 255]` |
| 5 | water | 水体 | `[0, 191, 255]` |

`255` 是唯一的标签栅格 NoData 值，不是可保存的 `class_code`，也不生成 GeoJSON 要素。

### 2.3 label GeoTIFF 规则

- 输出必须为单波段 `uint8` GeoTIFF；有效像元只能为 `0`–`5`，NoData 必须为 `255`。
- 输出 CRS 必须等于对应裁剪 TIFF 的 CRS，输出空间范围必须等于该裁剪 TIFF 的范围。
- 标签尺寸可能与裁剪 TIFF 尺寸不同。标签 transform 必须按尺寸比例重算，等价于：

  ```python
  label_transform = crop_transform * Affine.scale(
      crop_width / label_width,
      crop_height / label_height,
  )
  ```

  不得将裁剪 TIFF 的 transform 直接复用到已缩放的标签数组。
- ROI 外的标签像元必须写为 `255`。没有可验证的裁剪 TIFF CRS 或 transform 的单独 mask 不得发布 label GeoTIFF、自动 GeoJSON 或 Revision 0。

### 2.4 自动矢量化规则

- 自动矢量化只处理 `0`–`5`，跳过 `255`。
- 对每个类别，必须按 Rasterio 的 **4 邻域**连通规则识别 label 像元分量；对角接触不构成同一分量。
- 自动成果中小于 **16 个有效 label 像元** 的 4 邻域连通碎斑必须丢弃。该阈值按标签栅格像元数计算，不可替换为 EPSG:4326 的度面积；恰好 16 像元的碎斑保留。
- 每个保留分量必须单独矢量化，面再按矿山 ROI 裁切、清理空几何并重投影到 EPSG:4326；几何可为 `Polygon` 或 `MultiPolygon`。
- 自动 `feature_id` 必须可复现：同一 `result_id` 下，先按 `class_code` 分组，再将该类别的 4 邻域分量按其最小 `(row, column)` 的 row-major 顺序排序，并从 1 开始编号，格式固定为 `auto-{result_id}-{class_code}-{ordinal:04d}`。例如结果 `101` 的第一个草地分量是 `auto-101-0-0001`。实现不得依赖数据库自增 ID、文件名或不稳定的几何文本顺序。
- 此 16 像元规则仅约束自动矢量化，不能在人工保存时静默删除合法人工几何。

## 3. GeoJSON 要素与成果状态

每个返回或导出的分类要素必须具有以下服务器权威属性：

| 属性 | 规则 |
| --- | --- |
| `feature_id` | 要素的稳定标识。自动要素遵循 2.4 的固定 ID；人工新画要素由服务器分配，不能由客户端借此访问其他成果。正式人工 ID 在同一 `result_id` 的全部历史和当前修订中全局唯一且永不复用。 |
| `class_code` | 仅允许整数 `0`–`5`。 |
| `class_name` | 由 `class_code` 按固定类别表确定，客户端不可以不一致的名称覆盖。 |
| `source` | 仅为 `auto` 或 `manual`。 |
| `result_id` | 必须等于当前成果的 `{result_id}`，由服务器写入。 |
| `revision_no` | 必须等于承载该要素快照的版本号，由服务器写入。 |

保存请求中的每个 Feature 只允许提交 `geometry`、`properties.class_code` 和可选的 `properties.feature_id`；GeoJSON 的结构性 `type` 字段是协议字段，不属于业务属性。额外业务属性不被接受。服务端必须覆盖 `class_name`、`source`、`result_id` 与 `revision_no`，不采信客户端传入的同名值。

读取接口返回的 FeatureCollection 含完整的服务器权威属性，**不能原样提交**到保存接口。GeoView 必须在保存前投影为受限编辑 payload：每个 Feature 仅保留 `type`、`geometry` 和 `properties` 中的 `class_code`、可选 `feature_id`；必须剥离 `class_name`、`source`、`result_id`、`revision_no` 及其他业务属性。此投影是客户端适配契约，服务端仍必须再次拒绝未剥离的额外属性，不能因前端实现而降低校验。

客户端传入的 `feature_id` 若是当前 `base_revision_no` 中、且属于当前 `{result_id}` 的既有 ID，表示更新该既有要素；任何其他既有样式 ID 都必须拒绝。新画要素可以省略 `feature_id`，或传临时 `client-` 前缀 ID；这两种情形均由服务端生成并返回正式 ID，临时 ID 不得持久化。一个请求内所有非空 `feature_id`（既有、临时和任何其他形式）必须统一唯一；既有 ID 只能更新其在该基准修订、同一成果中的逻辑要素。Revision 0 的全部要素必须为 `source=auto`；任何已保存的 Revision `>=1` 中的全部要素都必须为 `source=manual`，包括从自动基线带入但未改动的要素。

`vector_status` 的公开终态仅为下列三种，字段是否可读必须严格服从此状态矩阵：

| vector_status | 自动基线 | 当前成果与版本 | `vector_error` 与保存行为 |
| --- | --- | --- | --- |
| `ready` | `auto_feature_collection` 为非空 FeatureCollection。 | `current_feature_collection` 为当前 FeatureCollection，`current_revision_no >= 0`；人工删除后当前集合可以为空。 | `vector_error=null`；允许按本契约保存。 |
| `ready_empty` | `auto_feature_collection` 为空 FeatureCollection。 | 初始 `current_feature_collection` 为空、`current_revision_no=0`；之后人工保存可以使当前集合非空，版本仍满足 `>=0`。 | `vector_error=null`；允许按本契约保存。 |
| `vector_failed` | `auto_feature_collection=null`。 | `current_feature_collection=null`，`current_revision_no=null`，不得创建 Revision 0。 | 保留成果与可读分类历史错误，但 `vector_error` 只能是脱敏错误代码或面向用户的简短说明，禁止绝对路径、stack、模型名、权重名或存储目录。保存一律返回 HTTP `409`，`data.details.error="vector_failed"`，且 `data.details` 至少含 `vector_status: "vector_failed"`、`save_allowed: false` 和脱敏原因。 |

因此，读取接口对 `ready` 与 `ready_empty` 返回两个 FeatureCollection；对 `vector_failed` 返回上述三个 `null` 字段而不是伪造空集合。既有 PNG 推理成果仍保持其原有成功语义。

v1 不定义面向客户端的处理中状态；若后续需要暴露处理中状态，必须先更新本契约并完成消费者评审。

## 4. 成果、修订与审计模型

存储形式可以是数据库字段或受控文件路径，但下列逻辑模型和不可变关系必须成立。

| 模型 | 必须关联/记录 | 不可违反的规则 |
| --- | --- | --- |
| `ClassificationResult` | `project_id`、`mine_fid`、`year`、`inference_job_id`、模型标识、label GeoTIFF、`auto.geojson`、当前版本、`vector_status`、`vector_error`。 | 每个 `(project_id, mine_fid, year, inference_job_id)` 组合只对应一条成果；前端不得只用 `mine_fid` 或 `year` 识别成果。`vector_failed` 时 label、自动基线和当前版本可以为空，但错误状态和脱敏错误必须保留。 |
| `ClassificationRevision` | 所属 `result_id`、`revision_no`、来源、创建人、创建时间、要素数和 GeoJSON 快照位置。 | 仅在 `ready` 或 `ready_empty` 时创建不可编辑的 Revision 0：其快照与自动基线一致且全部要素为 `auto`。每次人工保存创建 Revision `>=1`，快照中的全部要素为 `manual`，不能覆盖 `auto.geojson`。`vector_failed` 不得创建修订。 |
| `ClassificationEditAudit` | 保存人、保存时间、基准版本、生成版本、操作摘要、请求来源。 | 保存人必须来自服务器会话，不能采信请求体中的用户身份。 |

用户删除要素时，只能在新的 `ClassificationRevision` 中反映删除；不得物理抹除历史修订或审计记录。读取编辑器时，`ready` 与 `ready_empty` 的后端返回当前完整 `FeatureCollection`；前端不得把本地任意 GeoJSON 当作另一个成果直接保存。`vector_failed` 不返回当前集合且不得进入保存流程。

## 5. 当前会话与项目归属边界

- 当前仓库只有 `admin_user_id` 登录态，尚无项目成员、角色或多用户项目权限模型。v1 沿用这一现状：已登录管理员可在系统内操作项目；本文不把它描述成成员隔离或只读/编辑角色控制。
- 服务器从已验证会话取得操作者，不接受请求体中的 `actor`、用户 ID、服务器文件路径或任意 URL 作为授权依据。
- 读取、保存、导出时必须验证 `{result_id}` 的 `project_id` 等于 URL 中的 `{project_id}`；读取瓦片时必须验证 `{resource_id}` 属于该 URL 项目。任一不匹配都不得返回该成果、修订、GeoJSON 或瓦片内容，从而避免借跨项目 ID 读取对象。
- 未认证请求和项目归属不匹配的具体 HTTP 状态/既有响应格式沿用项目当前鉴权约定；新增接口不得通过不同错误细节泄露另一项目对象是否存在。

## 6. v1 HTTP 路由

下表定义 v1 路由；当前目标工作树的后端入口位于 `backend/applications/api/project.py`，GeoView 消费者位于 `frontend/src/api/classificationResults.js`。其他分支和部署环境的状态需另外核对。

| 方法和路径 | 用途 | v1 约束 |
| --- | --- | --- |
| `GET /api/projects/{project_id}/classification-results/{result_id}` | 编辑器首屏读取。 | 返回成果元数据、固定类别表、状态矩阵允许的自动/当前 FeatureCollection、`current_revision_no`、项目地图 manifest、`vector_status` 和 `vector_error`。 |
| `GET /api/projects/{project_id}/classification-results/{result_id}/revisions` | 读取修订摘要。 | 只返回版本列表与作者/时间摘要，不返回历史版本回滚能力。 |
| `POST /api/projects/{project_id}/classification-results/{result_id}/revisions` | 保存编辑成果。 | 只使用绑定 URL 成果的受限 FeatureCollection 与 `base_revision_no`；通过后创建一个新修订和审计记录。它不是通用 GeoJSON 文件导入、外部 URL 导入或服务器路径导入接口。 |
| `POST /api/projects/{project_id}/classification-results/{result_id}/export` | 下载当前成果。 | 服务端根据当前版本生成 GeoJSON；不得接受或导出客户端上传的 `features`。 |
| `GET /api/projects/{project_id}/map-resources/{resource_id}/tiles/{z}/{x}/{y}.png` | GeoView 受控读取底图瓦片。 | 必须验证会话和资源归属；编辑器只能消费地图 manifest 中提供的 `api_tile_url`。 |
| 既有分类历史接口的扩展 | 分类历史卡片跳转。 | 项目关联记录只增补 `result_id`、`vector_status`、`vector_error`；没有矢量成果的旧记录保持可读且这些字段为空。 |

### 6.1 最小 JSON 数据形状

读取成果的 `data` 至少包含下列成员；下例仅表示 `ready_empty`，不是对所有状态都存在两个 FeatureCollection 的承诺。`map_manifest` 的其他字段仍由既有项目地图契约定义。

```json
{
  "result_id": 102,
  "project_id": 1,
  "mine_fid": 1001,
  "year": 2026,
  "inference_job_id": "…",
  "vector_status": "ready_empty",
  "vector_error": null,
  "classes": [
    {"class_code": 0, "class_name": "grassland", "class_name_zh": "草地", "rgb": [0, 255, 0]},
    {"class_code": 1, "class_name": "forest", "class_name_zh": "林地", "rgb": [0, 128, 0]},
    {"class_code": 2, "class_name": "building", "class_name_zh": "建筑", "rgb": [255, 0, 0]},
    {"class_code": 3, "class_name": "road", "class_name_zh": "道路", "rgb": [255, 255, 0]},
    {"class_code": 4, "class_name": "bareground", "class_name_zh": "裸地", "rgb": [255, 0, 255]},
    {"class_code": 5, "class_name": "water", "class_name_zh": "水体", "rgb": [0, 191, 255]}
  ],
  "auto_feature_collection": {"type": "FeatureCollection", "features": []},
  "current_feature_collection": {"type": "FeatureCollection", "features": []},
  "current_revision_no": 0,
  "map_manifest": {
    "api_tile_url": "/api/projects/1/map-resources/12/tiles/{z}/{x}/{y}.png"
  }
}
```

`map_manifest.api_tile_url` 的唯一 v1 形状是 `/api/projects/{project_id}/map-resources/{resource_id}/tiles/{z}/{x}/{y}.png`；其中 `{z}`、`{x}`、`{y}` 是地图客户端替换的瓦片占位符。无可用底图时该字段必须为 `null`，而不是服务器磁盘路径、公开对象存储 URL 或空字符串。GeoView 必须将该相对路径按已认证后端 `BASEURL` 解析，而不是按静态页面所在端口解析；MapLibre 的瓦片请求必须携带现有会话凭据（`credentials: include`）。部署若改为 `/api` 同源反代，也必须保持这一鉴权语义。现有 Miner `/tiles/projects/...` 的旧 `tile_url` 没有本 v1 所要求的会话校验，GeoView 不得将其当作受控瓦片源，也不在本轮改造该旧路由。`vector_failed` 的读取响应必须将 `auto_feature_collection`、`current_feature_collection` 与 `current_revision_no` 都返回为 `null`，并遵守第 3 节的脱敏错误规则。

修订摘要的 `data` 至少包含 `result_id`、`current_revision_no` 和 `revisions`。每个摘要包含 `revision_no`、`source`、作者摘要、`created_at` 和 `feature_count`，不包含回滚入口。

保存请求的 JSON body 恰有以下业务输入：

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
        "geometry": {"type": "Polygon", "coordinates": []}
      }
    ]
  }
}
```

示例中的 `coordinates` 仅省略具体合法环以突出字段白名单；实际保存必须满足第 7 节的几何校验。`base_revision_no` 必须是非负安全整数。一个请求内所有非空 `feature_id` 必须统一唯一；既有 ID 仅可指向同一 `{result_id}` 的 `base_revision_no` 中既有逻辑要素，正式人工 ID 由服务端生成、在该成果所有历史和当前修订中唯一且永不复用；临时 `client-` ID 只用于本次请求且不得持久化。服务端不得自动修补自交、未闭合环、ROI 外或其他无效几何，而是全量拒绝。该 body 只能代表 URL 已绑定的单一 `{result_id}`；不得上传通用 GeoJSON 文件、`multipart/form-data` 文件、外部 URL、对象存储 URL 或服务器路径。保存成功的 `data` 至少返回 `result_id`、新 `revision_no`、新的 `current_revision_no` 和服务器规范化后的当前 `feature_collection`。导出请求没有 `features` 业务输入，返回的下载内容只能是服务器当前修订生成的 GeoJSON。

## 7. 409 与 422 语义

| HTTP status | 何时使用 | `data.details` 的最小内容 | 禁止的行为 |
| --- | --- | --- | --- |
| `409 Conflict` | 保存时提交的 `base_revision_no` 不等于当前 `current_revision_no`。 | `base_revision_no` 与 `current_revision_no`。 | 不创建修订、不更新当前版本、不自动合并或自动覆盖。 |
| `409 Conflict` | 目标成果为 `vector_failed`。 | `data.details.error="vector_failed"`，以及 `vector_status="vector_failed"`、`save_allowed=false`、脱敏原因。 | 不创建 Revision 0/人工修订/审计记录，不把失败伪装为空成果。 |
| `422 Unprocessable Entity` | FeatureCollection 缺失/结构错误，`class_code` 不是 `0`–`5`，把 `255` 当作类别，几何或 ID 校验失败，或违反下述容量、空间范围约束。 | 每个失败项的字段/要素定位和可读原因；不得回显服务器路径、stack、模型或存储目录。 | 不部分保存、不创建审计记录、不更改当前版本。 |

保存前必须完整校验受限 FeatureCollection，且任一失败均为全量失败：

- FeatureCollection 与每个 Feature 的结构必须有效；每个几何必须是非空、有效的 `Polygon` 或 `MultiPolygon`。
- 每个 position 必须是恰有两个有限数值元素的数组 `[longitude, latitude]`；经纬度必须为 EPSG:4326 的经度 `[-180, 180]`、纬度 `[-90, 90]`，拒绝 Z 值及其他额外维度。每个几何必须完全被该 `{result_id}` 对应 ROI 的 `covers` 包含，不允许 buffer、容差或“相交即可”的宽松判断。
- 每个既有 `feature_id` 必须属于提交的 `base_revision_no` 且属于当前 `{result_id}`；新要素只可省略 ID 或使用临时 `client-` 前缀 ID；一个请求内所有非空 ID 必须统一唯一。客户端不得提交或覆盖 `class_name`、`source`、`result_id`、`revision_no` 等服务器权威属性。
- 单次保存请求的 UTF-8 JSON body 不得超过 **10 MiB**（`10 × 1024 × 1024` bytes）；Feature 总数不得超过 **2,000**；每个 Feature 的全部环中坐标位置总数不得超过 **5,000**；整个 FeatureCollection 的坐标位置总数不得超过 **100,000**。坐标位置按请求中出现的每个 `[longitude, latitude]` 计数，包含闭合环重复的末端位置。

API 测试必须分别覆盖：超过 10 MiB、超过 2,000 个要素、单要素超过 5,000 个顶点、总顶点超过 100,000 的四个边界；每一例均断言 HTTP `422`、无部分保存、无新修订、无审计记录、当前版本不变。测试还必须覆盖 NaN/Infinity、超经纬度、非面/无效面、ROI 外面、跨成果或跨基准版本 `feature_id`，以及 `vector_failed` 的 `409` 响应。

## 8. v1 明确不做项

- 不修改既有 PNG 预览的命名、浏览路径、模型输出分辨率或既有推理成功语义。
- 不重新训练模型、不上传 PTH、不改变上述六类类别代码。
- 不将 Vue 改为 React，不新增 IMG 等输入格式，也不承诺新格式的模型精度。
- 不做浏览器端大底图上传、断点续传、单终端切片或在线大底图切片；本轮只复用已有项目瓦片。
- 不支持通用 GeoJSON 文件导入或任意 GeoJSON、服务器文件路径、URL 作为成果保存/导出来源；唯一例外是本契约第 6 节规定、已绑定 URL 成果的受限 JSON 编辑 payload。
- 不实现历史版本回滚、自动合并、多人实时 GIS 协作或物理删除审计历史。
- 不新增项目成员、只读/编辑角色、多用户权限隔离或基于成员的项目授权；这些需要独立的数据模型、迁移和安全评审，不能被本 v1 的 `admin_user_id` 会话现状替代。
- 不把成果编辑器放入 Miner；本轮由 GeoView 消费受鉴权成果与地图接口。
- 不新增 KML/Shapefile 批量导出、任意底图文件直链或公开瓦片访问契约；v1 仅保证服务端生成当前 GeoJSON 及受控 PNG 瓦片读取。
- 不把现有 Miner `/tiles/projects/...` 旧路由改造成受控瓦片接口；本轮 GeoView 仅使用新 `api_tile_url`，旧 `tile_url` 不构成安全或兼容承诺。

## 9. 任务 0 开发环境记录

这一节是可重复测试的记录要求，不是向客户端公开的新 API，也不把未在仓库中固定的运行时版本捏造成已锁定版本。每次生成或验证 fixture、实现本契约的测试报告都必须记录以下信息：

| 范围 | 当前可核查基线 | 测试/发布记录必须补充 |
| --- | --- | --- |
| Python/GIS | `backend/requirements.txt` 声明 `rasterio>=1.3.10,<2` 与 `shapely==1.8.5.post1`；Compose 为 MMSeg310 设置 GDAL/PROJ 数据目录。 | 实际 Python、Rasterio、GDAL、PROJ 版本及执行环境。 |
| Node/npm | `miner/package.json` 声明 Node `^20.19.0 || >=22.12.0`；GeoView 的 `frontend/package.json` 未声明 Node/npm engine。 | GeoView 与 Miner 分别实际使用的 Node、npm 版本和锁文件状态。 |
| Docker 镜像 | `image_bundle.env` 当前声明 `yunnan-runtime:current`、`yunnan-inference-worker:current` 与 MySQL `8.0.30-8.6` 镜像标签。 | 运行的镜像 tag 与不可变 digest；不得只记录 `current` 作为可复现依据。 |
| 模型与大数据挂载 | Compose 将后端源码只读挂载到 `/app/backend`，项目存储挂载到 `/project_storage`，离线地图输入挂载到 `/project_storage/incoming/dali`。 | 模型文件标识与 SHA-256、fixture/大数据的宿主挂载根、读写权限；不把权重、真实影像或生产路径写入客户端请求。 |

上述清单补齐后才可以声称某一次后端空间测试或离线验收可复现；它不要求把模型、镜像 tar 或数据瓦片纳入普通 Git 跟踪。
