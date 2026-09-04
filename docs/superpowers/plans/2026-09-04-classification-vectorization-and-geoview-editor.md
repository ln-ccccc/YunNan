# 分类成果矢量化与 GeoView 编辑实施计划

> 本文是实施前的审核稿。除本文外，尚未修改任何业务代码、依赖或部署配置。

**目标：** 将现有“矿山地物分类”推理产生的像素级 PNG 成果，新增为可追溯、可编辑、可导出的项目矢量成果；在现有 Vue GeoView 中完成查看、编辑、保存、冲突处理和 GeoJSON 导出。

**实施方式：** 保持 Vue 技术栈，不迁移 React；不接入在线训练；不改变既有影像导入、推理任务、PNG 浏览和历史记录主链路。新功能以独立的分类成果记录、接口和 GeoView 页面接入，避免把地图编辑逻辑塞入现有 PNG 预览组件。

**技术边界：**

- 后端：Flask、Rasterio、Shapely、现有任务队列和项目权限体系。
- 前端：Vue 3、Vue Router、Element Plus、MapLibre GL JS 和 MapLibre-Geoman Free。
- 编辑对象：推理结果自动生成的 GeoJSON，不是原始 KML，也不是底图文件。
- v1 导出格式：GeoJSON；Shapefile、KML、批量导出作为后续需求，不纳入本轮。
- v1 编辑操作：修改顶点、拖动、绘制多边形、删除要素、修改类别、保存为新版本；不做自动拓扑修复、多人实时协同编辑、分割合并工具和撤销栈。
- 默认最小斑块阈值：16 个分类像素。该值作为服务内部常量和记录元数据，不向甲方暴露为随意可调参数；试运行后可基于样本调整。

---

## 1. 已确认的范围与不纳入项

### 本轮必须完成

1. 从已有分类掩膜生成带正确空间参考的 label GeoTIFF。
2. 将 label GeoTIFF 自动矢量化，并按矿山边界裁剪、去除极小斑块、转换为 EPSG:4326 GeoJSON。
3. 将自动成果、模型信息、推理任务、空间资源、类别体系和版本信息持久化。
4. 向项目授权用户提供读取、保存新版本、导出 GeoJSON 的接口。
5. 在 GeoView 中叠加项目底图和分类矢量，提供受限编辑与保存冲突提示。
6. 在既有分类历史卡片上增加“查看/编辑矢量成果”入口；没有可用矢量成果时明确显示状态，不伪装为可编辑。
7. 完成单元、接口、前后端联调和离线部署验证。

### 本轮明确不做或暂缓

| 项目 | 结论 | 原因与后续条件 |
| --- | --- | --- |
| Vue 改 React | 不做 | 与矢量编辑无直接必要关系；会扩大路由、构建、依赖和回归范围。MapLibre-Geoman Free 可在 Vue 中使用。 |
| 模型在线训练、持续训练、训练接口 | 不做 | 甲方 4070 设备不适合不受控的大规模在线训练；继续训练还可能发生灾难性遗忘，不能承诺训练进展或精度。 |
| 新增 IMG 等输入格式并承诺同等精度 | 不做 | 当前模型以 TIFF 数据分布训练；直接换格式可能改变波段、位深、投影或预处理，不能承诺精度。若后续要支持，需单列数据规范、验证集和精度验收。 |
| 浏览器端大底图上传、断点续传、单终端切片 | 暂缓 | 这不是矢量编辑的前置条件，且受浏览器磁盘、网络、超时和本地服务能力限制。建议离线部署时将经预处理的底图放入指定目录，由管理员登记入库；若甲方坚持单终端处理，需单列桌面工具/本地服务方案和验收。 |
| 在线大底图切片 | 暂缓 | 可做，但需要任务化、磁盘配额、切片状态、失败恢复和性能验证。本轮仅复用已有项目瓦片，以不阻断编辑。 |
| 实时多人同图编辑 | 不做 | 需要 WebSocket、锁定/合并规则、冲突解决和审计扩展；本轮使用乐观锁，避免静默覆盖。 |
| KML/Shapefile 批量导出 | 暂缓 | v1 先以标准 GeoJSON 闭环；若要加 GDAL/OGR 格式转换与编码、字段规范测试，单列需求。 |

---

## 2. v1 数据和接口契约

在任何人写业务代码前，先将以下契约写入 docs/vector-result-v1-contract.md，评审后冻结。接口字段、类别代码和错误语义变更必须同步更新该文档。

### 2.1 分类和几何约定

| 字段 | 约定 |
| --- | --- |
| 坐标系 | API 和导出文件统一为 EPSG:4326；中间 label GeoTIFF 保留原裁剪 TIFF 的 CRS 和正确仿射变换。 |
| 类别代码 | 0 草地，1 林地，2 建筑，3 道路，4 裸地，5 水体；名称和调色板来自现有 mmseg_segmentation.py。 |
| NoData | 标签栅格使用 255；不生成 NoData 要素。 |
| 矢量要素属性 | feature_id、class_code、class_name、source=auto 或 manual、result_id、revision_no。 |
| 自动成果 | 每个 project_id、mine_fid、year、推理任务组合生成一条 ClassificationResult；不能用 fid 或 year 单独作为前端身份。 |
| 可编辑成果 | 后端返回当前版本完整 FeatureCollection；前端不得将本地任意 GeoJSON 当作分类成果保存。 |
| 删除语义 | 用户删除的要素不从审计记录中物理抹除；保存为新的成果版本。 |
| 冲突语义 | 保存请求必须携带 base_revision_no。若当前版本已变化，返回 HTTP 409 和最新版本号；前端不自动覆盖。 |

### 2.2 文件产物和持久化模型

现有 PNG 产物继续保留，不修改其命名和浏览路径。新增以下产物和记录：

| 内容 | 建议位置/模型 | 说明 |
| --- | --- | --- |
| 原始标签栅格 | 现有推理输出目录的 <fid>+<year>_label.tif | 单波段 uint8，0-5 为类别，255 为 NoData，带裁剪图的正确 transform/CRS。 |
| 自动矢量基线 | 项目推理成果目录下按 result_id 组织的 auto.geojson | 由 label GeoTIFF 自动生成，只读基线，可复现。 |
| 当前编辑版本 | ClassificationRevision 的 GeoJSON 快照或受控文件路径 | 每次保存创建新版本，不覆盖 auto.geojson。 |
| 成果主记录 | ClassificationResult | 关联项目、矿山、年份、推理任务、模型、标签文件、自动成果、当前版本、状态和错误信息。 |
| 修订记录 | ClassificationRevision | revision_no、来源、创建人、创建时间、要素数和快照位置。 |
| 审计记录 | ClassificationEditAudit | 保存人、保存时间、基准版本、生成版本、操作摘要和请求来源；不采信客户端声明的用户身份。 |

路径具体由后端服务集中生成，不在 Vue 组件里拼接，也不让客户端传服务器文件路径。

### 2.3 v1 HTTP 接口

所有接口沿用项目鉴权与项目成员权限校验，服务器从会话读取操作者身份。

| 方法和路径 | 用途 | 关键返回/校验 |
| --- | --- | --- |
| GET /api/projects/{project_id}/classification-results/{result_id} | 编辑器首屏读取 | 成果元数据、类别表、自动/当前 GeoJSON、current_revision_no、项目地图 manifest、状态。 |
| GET /api/projects/{project_id}/classification-results/{result_id}/revisions | 读取修订摘要 | 仅返回版本列表和作者/时间摘要；v1 不做历史版本回滚。 |
| POST /api/projects/{project_id}/classification-results/{result_id}/revisions | 保存编辑成果 | 仅接受 FeatureCollection、base_revision_no；校验项目、类别、几何、要素数和大小。成功创建新版本。 |
| POST /api/projects/{project_id}/classification-results/{result_id}/export | 下载当前成果 | 服务端根据当前版本生成 GeoJSON；不接受客户端上传 features。 |
| GET /api/projects/{project_id}/map-resources/{resource_id}/tiles/{z}/{x}/{y}.png | GeoView 受控加载底图瓦片 | 验证项目权限和资源归属；用于编辑器优先使用的 api_tile_url。 |
| 既有分类历史接口扩展 | 历史卡片跳转 | 每条项目关联记录附加 result_id、vector_status、vector_error；无成果时为空。 |

说明：路径中的花括号只是路由参数说明，实际实现遵循现有 Flask Blueprint 风格。错误响应沿用既有响应结构，并在 data.details 中提供冲突或校验细节；前端 Axios 封装需保留 HTTP status，供 409/422 区分处理。

---

## 3. 架构和调用顺序

~~~text
既有 KML ROI 推理
      |
      v
裁剪 TIFF + 512 预测标签
      |
      +-- 保持已有 PNG 结果，原流程不变
      |
      v
写入带空间参考的 label GeoTIFF
      |
      v
Rasterio shapes 自动矢量化
      |
      +-- 逐类提取
      +-- 裁剪到矿山 ROI
      +-- 丢弃小于 16 像素的碎斑
      +-- 重投影到 EPSG:4326
      |
      v
ClassificationResult + Revision 0 + auto.geojson
      |
      +--------------------------------------+
      |                                      |
      v                                      v
分类历史卡片                        GeoView 矢量编辑页面
                                           |
                                           v
                            乐观锁保存新 Revision + 审计
                                           |
                                           v
                                   服务端导出 GeoJSON
~~~

关键空间计算：预测 PNG 的 512 乘 512 尺寸不能直接复用原裁剪 TIFF 的 transform。生成 label GeoTIFF 时必须按原裁剪 TIFF 宽高与标签宽高计算缩放后的 transform。实施代码会以等价于下式的逻辑验证：

~~~python
label_transform = crop_transform * Affine.scale(
    crop_width / label_width,
    crop_height / label_height,
)
~~~

不得调用现有 save_with_georeference 中“直接复用 reference transform”的方式保存已缩放的 512 像素标签，否则空间位置和范围会错误。

---

## 4. 代码边界与文件归属

### 后端/GIS 负责人的边界

- backend/applications/kml_roi/raster_ops.py：标签 TIFF 读取、写入、transform 计算的纯空间工具。
- backend/applications/kml_roi/tiles.py：在既有 PNG 分发后增加 label TIFF 分发，不改既有 PNG 名称和返回字段。
- backend/applications/kml_roi/vectorization/classification.py：新增，自动矢量化、裁剪、过滤和 GeoJSON 序列化的纯函数。
- backend/applications/models/classification_result.py：新增三个数据模型。
- backend/applications/project_hub/classification_results.py：新增，成果创建、读取、修订、导出和审计服务。
- backend/applications/project_hub/inference_results.py：推理发布成功后触发成果记录和 Revision 0 创建；矢量化失败不能把原 PNG 推理成功改成失败。
- backend/applications/inference/worker.py：把 inference_job_id 传入项目成果发布。
- backend/applications/api/project_classification.py：新增成果接口 Blueprint。
- backend/applications/api/project.py：只做受控瓦片端点的最小接入。
- backend/applications/api/analysis.py：只扩展历史记录的 result_id/vector_status，不复制成果服务逻辑。
- backend/applications/api/__init__.py、backend/applications/models/__init__.py、backend/applications/inference/app.py：注册新 Blueprint/模型，保证 worker 建表时可发现新模型。
- backend/test_classification_vectorization.py、backend/test_classification_results_api.py：新增后端测试。

### GeoView 前端负责人的边界

- frontend/src/api/classificationResults.js：新增，封装成果读取、保存、版本摘要、导出。
- frontend/src/utils/classificationResultContext.mjs：新增，严格读取和校验 project_id、result_id 路由参数。
- frontend/src/components/ClassificationVectorMap.vue：新增，只负责 MapLibre 初始化、底图/矢量图层、地图编辑事件和销毁；不直接调用 API。
- frontend/src/views/mainfun/ClassificationResultEditor.vue：新增，负责加载状态、权限/错误状态、图例、类别选择、保存、冲突提示和导出。
- frontend/src/router/index.js：新增受鉴权编辑器路由。
- frontend/src/components/ImgShow.vue：为已有项目分类成果增加查看/编辑入口；无 result_id 时只显示状态说明。
- frontend/src/utils/interpretationContext.mjs、frontend/src/utils/getUploadImg.js：仅扩展卡片数据中的 result_id/vector_status。
- frontend/src/api/request.js：最小化扩展错误对象，使 409 和 422 可被页面识别；不得破坏现有接口返回方式。
- frontend/package.json、frontend/package-lock.json：固定并锁定经验证的地图依赖版本。
- frontend/test/classification-result-context.test.mjs 及必要的组件/接口测试：新增前端测试。

### 共同但只能指定一位负责人修改的热点

| 文件/领域 | 唯一负责人 | 另一人如何协作 |
| --- | --- | --- |
| docs/vector-result-v1-contract.md | 后端负责人起草，前端负责人评审 | 合并后才能改接口字段。 |
| docker/Dockerfile.inference-gpu、docker-compose.prod.yml、frontend Docker 构建依赖 | 后端负责人 | 前端负责人提供依赖清单和构建验证结果，不直接并发改同一文件。 |
| 既有历史卡片数据结构 | 前端负责人提出字段消费方式，后端负责人增加字段 | 先以固定 fixture 联调，避免双方同时改历史接口。 |
| 路由和页面入口 | 前端负责人 | 后端只提供 result_id/status，不改 Vue 路由。 |

Miner 继续负责项目、影像、瓦片及已有任务入口；本轮不把推理成果编辑组件放进 Miner。GeoView 只消费项目地图 manifest 与受控成果接口。

---

## 5. 分步实施任务

每项任务都应先写失败测试，再写最小实现，再运行该任务列出的验证。每个 PR 只包含一个可审核的能力，不夹带重构、格式化或依赖升级。

### 任务 0：冻结契约、准备可重复样本和协作基线

**负责人：** 后端负责人主办，前端负责人评审。  
**前置：** 不需要业务代码修改。  
**新增：** docs/vector-result-v1-contract.md，docs/vector-result-v1-fixture.md，测试小样本目录说明。

步骤：

1. 将第 2 节接口、类别、状态、错误和默认阈值整理为单独契约文档。
2. 选取一份小尺寸、可脱敏的裁剪 TIFF、预测 mask、矿山 ROI 和期望 GeoJSON 作为固定测试样本；样本不含模型权重或大底图。
3. 明确两类失败预期：无空间参考的 mask 不得发布成果；非法类别或非法几何不得保存版本。
4. 建立开发环境清单：Python/Rasterio 版本、Node/npm 版本、Docker 镜像 tag、模型文件和大数据的挂载路径。
5. 核查 Git 基线。当前工作区根目录不是可用于统一 PR 的干净 Git 仓库，且 Miner 是独立且有未提交改动的仓库。正式并行开发前，项目负责人需决定使用哪个现有仓库承载 backend/frontend 的 PR，或明确授权初始化/迁移统一仓库；在此之前不得默认执行 git init、重置或清理 Miner。

验收：

- 契约文档通过两人评审，接口示例和错误状态没有歧义。
- 小样本能在本地测试中被读取，不依赖 1GB 以上资产。
- 模型权重、镜像 tar、数据瓦片没有进入普通 Git 跟踪范围。

### 任务 1：生成空间正确的 label GeoTIFF

**负责人：** 后端负责人。  
**依赖：** 任务 0。  
**涉及文件：** raster_ops.py、tiles.py、test_classification_vectorization.py。

先写测试：

1. 用已知 1000 乘 500 的裁剪栅格和 512 乘 512 的标签数组，断言输出的 bounds 与裁剪栅格 bounds 一致。
2. 断言输出为单波段 uint8，类别值和 NoData 值正确。
3. 断言预测范围外的像素为 255，矿山边界内类别值保持不变。
4. 断言既有 PNG 和 mask 输出名称、数量及调用路径不变。

实现：

1. 在 raster_ops.py 增加纯函数，接收标签数组、裁剪 TIFF 元信息、ROI geometry，并写入 label GeoTIFF。
2. 使用裁剪 TIFF 的 CRS，并根据输入/输出尺寸重算 transform。
3. 使用已有 geometry rasterize 能力把 ROI 外区域写成 255。
4. 在 tiles.py 的结果分发阶段新增 label TIFF；scan/cleanup 逻辑允许保留该 TIFF。
5. 不修改模型输出分辨率、不重训模型、不改变旧 PNG 预览。

验证命令：

~~~text
python -m unittest -v test_classification_vectorization test_kml_roi_pipeline
~~~

完成标准：

- 固定样本的输出 bounds、CRS、NoData、类别值全部断言通过。
- 既有 KML ROI 管线测试仍通过。

### 任务 2：自动矢量化、成果模型和 Revision 0

**负责人：** 后端负责人。  
**依赖：** 任务 1。  
**涉及文件：** 新增 vectorization/classification.py、classification_result.py、classification_results.py；修改 inference_results.py、worker.py、models/__init__.py、inference/app.py。

先写测试：

1. 输入两类标签块，断言产生对应 class_code 的 Polygon 或 MultiPolygon。
2. 输入 ROI 外的类别块，断言裁剪后不进入 GeoJSON。
3. 输入小于 16 像素的碎斑，断言被移除。
4. 输入无效 transform/CRS 或空结果，断言成果状态为 vector_failed 或 ready_empty，且 PNG 发布仍为成功。
5. 推理成功发布时，断言 ClassificationResult 与 Revision 0 均关联正确 project_id、mine_fid、year、inference_job_id。

实现：

1. 用 rasterio.features.shapes 对 label GeoTIFF 按类别提取几何。
2. 用 Shapely 与矿山 ROI 相交，清理空几何；必要时将有效 Polygon 合并为 MultiPolygon。
3. 重投影至 EPSG:4326，填写固定 properties。
4. 写入 auto.geojson，创建 ClassificationResult 和不可编辑的基线 Revision 0。
5. 扩展项目推理发布逻辑，使自动矢量化作为后处理。若该后处理失败，记录错误和状态，但绝不回滚已经成功的 PNG 成果或把推理任务误报失败。
6. 把 worker 当前 job.id 显式传入发布服务，避免通过不稳定的文件名推断来源。
7. 仅新增表，避免对既有表做无审查的大型 schema 改写；上线前在测试库验证 create_all 能创建新表。

验证命令：

~~~text
python -m unittest -v test_classification_vectorization test_project_inference_results test_inference_jobs test_inference_worker_app
~~~

完成标准：

- 相同输入重复运行可得到稳定类别和几何数量。
- 推理 PNG 成果与矢量状态可以独立失败/成功并可追溯。
- 不会因矢量化异常破坏现有历史或下载行为。

### 任务 3：读取 API、分类历史入口数据和安全底图瓦片

**负责人：** 后端负责人。  
**依赖：** 任务 2。  
**涉及文件：** 新增 api/project_classification.py；修改 api/__init__.py、api/analysis.py、api/project.py、project_map.py、test_classification_results_api.py。

先写测试：

1. 项目成员能读取属于本项目的 result_id；非成员、跨项目 result_id 和不存在记录返回正确权限/404 语义。
2. 分类历史中有成果的记录带 result_id/vector_status；老记录没有矢量成果时不报错。
3. 受控瓦片端点只允许项目成员读取所属资源。
4. 读取接口返回的 map.api_tile_url 与当前成果关联底图资源一致。
5. 结果为 vector_failed 时读取 API 返回可解释错误和 PNG 可用状态，而不是空白 500。

实现：

1. 新 Blueprint 仅负责分类成果读、修订、导出等接口；不要把业务实现堆进 project.py。
2. 在 project.py 增加最小受控瓦片路由，验证项目和资源归属后复用已有瓦片读取能力。
3. 在项目地图 manifest 中增加 api_tile_url；保留既有 tile_url 兼容 Miner 和旧页面。
4. 在 analysis.py 批量关联成果记录，避免对每个历史卡片做一次数据库查询。
5. 所有结果 API 只接受服务端 result_id，不能接受文件系统路径或任意 URL。

验证命令：

~~~text
python -m unittest -v test_classification_results_api test_interpretation_api test_project_map
~~~

完成标准：

- GeoView 只靠受鉴权接口就能获得一份地图 manifest 和编辑基础数据。
- 老项目、老历史记录和旧 PNG 卡片不因新增字段崩溃。

### 任务 4：保存版本、审计和服务端 GeoJSON 导出

**负责人：** 后端负责人。  
**依赖：** 任务 3。  
**涉及文件：** classification_results.py、project_classification.py、test_classification_results_api.py。

先写测试：

1. 用正确 base_revision_no 保存合法 FeatureCollection，断言生成 Revision 1 和审计记录。
2. 两次以同一 base_revision_no 保存，第二次断言返回 409，且现有版本不被覆盖。
3. class_code 不在 0 至 5、geometry 为空/自交、跨越允许范围、FeatureCollection 超出大小限制时，断言返回 422。
4. 导出端点读取服务端当前版本，断言不会把客户端请求体作为导出内容。
5. 审计记录中的 actor 来自登录会话而非请求 body。

实现：

1. 对 FeatureCollection 设置大小、要素数、类别、几何类型和空间范围的明确上限；阈值写在服务常量及契约中。
2. 使用事务创建 Revision、更新 current_revision 和追加 Audit，确保三者一致。
3. 保存使用乐观锁；冲突时只返回提示和最新版本号，不实现自动合并。
4. 导出当前有效版本为标准 GeoJSON，使用安全 Content-Disposition 文件名。
5. v1 不实现历史版本回滚，但保留历史摘要读取能力，避免以后破坏数据模型。

验证命令：

~~~text
python -m unittest -v test_classification_results_api
~~~

完成标准：

- 两个浏览器或两个用户并发保存时，后保存者不会静默覆盖前者。
- 每一个编辑保存都可查到人、时间、来源版本和目标版本。
- 非法数据不会写入数据库或覆盖当前成果。

### 任务 5：前端依赖、数据层和路由骨架

**负责人：** GeoView 前端负责人。  
**依赖：** 任务 0；可与任务 1、2 并行。  
**涉及文件：** frontend/package.json、frontend/package-lock.json、api/classificationResults.js、utils/classificationResultContext.mjs、router/index.js、api/request.js、前端测试文件。

先写测试：

1. 只有 project_id 和 result_id 都为合法整数时，路由上下文解析成功。
2. API 模块向正确路径发送读取、保存和导出请求。
3. request.js 对 409、422 保留 status 与后端 details，普通既有请求仍保持原返回语义。
4. 路由未登录时仍执行现有登录守卫。

实现：

1. 固定 MapLibre GL JS 与 MapLibre-Geoman Free 的经验证版本，并提交 package-lock，不使用 latest。
2. 在 Vue CLI/Webpack 构建中配置 MapLibre 的 ESM worker URL；以项目 lockfile 版本为唯一真相。
3. 新增受鉴权路由：/classification-results/editor?project_id=...&result_id=...。
4. 新增 API 模块和参数解析模块；禁止在页面里散写 API URL。
5. 不修改现有分割、指数、项目页面的渲染逻辑。

验证命令：

~~~text
npm run build
npm test
~~~

完成标准：

- 依赖安装后 Vue 工程可以生产构建。
- 访问非法路由参数不会请求后端或生成错误路径。
- 前端可用 fixture/mock 响应独立开发，不阻塞后端 PR。

### 任务 6：只读 GeoView 地图和分类历史入口

**负责人：** GeoView 前端负责人。  
**依赖：** 任务 3 的读取 API；页面布局可先用 fixture 完成。  
**涉及文件：** ClassificationResultEditor.vue、ClassificationVectorMap.vue、ImgShow.vue、interpretationContext.mjs、getUploadImg.js。

先写测试：

1. 有 result_id 且 vector_status 为 ready 的历史卡片显示“查看/编辑矢量成果”。
2. 无 result_id 或 vector_failed 的卡片不显示误导性编辑入口，而显示可查看 PNG/失败说明。
3. 页面加载成功时，将 api_tile_url、成果 GeoJSON 和类别图例交给地图组件。
4. 页面加载、空成果、无权限、后端异常四种状态都有可见提示，不只输出控制台错误。

实现：

1. ClassificationVectorMap.vue 只接收 props 并 emit 编辑后的 FeatureCollection，不发后端请求。
2. 初始状态以自动或当前成果绘制填充面和描边；点击/选中显示类别。
3. 底图仅使用服务端返回的 api_tile_url 和 bounds；前端不拼本地磁盘路径。
4. Editor 页面显示成果元数据：矿山、年份、模型、生成时间、当前版本、自动/人工来源说明。
5. 在 ImgShow 的既有项目分类卡片上增加跳转，不改变已有 PNG 删除和预览能力。

验证：

1. npm run build。
2. 使用浏览器手工验证：打开编辑器、缩放移动、底图加载、分类图例和一个有成果/一个无成果的历史卡片。
3. 检查浏览器 Network：没有泄漏服务器本地路径，受控瓦片请求带项目范围。

完成标准：

- 一位无地图经验的验收人员能从历史卡片打开对应成果，并看到正确位置、正确颜色和正确的项目底图。
- 老 PNG 预览卡片仍正常。

### 任务 7：GeoView 编辑、保存冲突和导出

**负责人：** GeoView 前端负责人。  
**依赖：** 任务 4。  
**涉及文件：** ClassificationResultEditor.vue、ClassificationVectorMap.vue、classificationResults.js 及测试。

先写测试：

1. 编辑事件后页面处于“有未保存修改”状态；保存成功后状态恢复。
2. 用户只能选择约定类别，不能提交未知类别。
3. API 返回 409 时，页面清晰提示“服务器已有新版本”，提供刷新成果按钮；不自动重试覆盖。
4. API 返回 422 时，页面显示后端校验信息且保留本地编辑内容。
5. 导出按钮仅调用服务端导出并触发浏览器下载。

实现：

1. 使用 MapLibre-Geoman Free 提供绘制、编辑、删除的受限工具栏；只开放 v1 已确认的操作。
2. 选择/新绘制要素时显示类别下拉框，并写入 feature.properties.class_code/class_name。
3. 保存时调用任务 4 接口，传递当前 FeatureCollection 和 base_revision_no。
4. 成功保存后以服务端返回的 revision_no 更新页面，重新载入规范化后的成果。
5. 只给有项目编辑权限的用户显示保存、绘制和删除控件；只读用户可查看和导出。
6. 不实现页面关闭前浏览器原生强拦截；仅在路由内跳转时给未保存修改提示，避免复杂且不可靠的全局行为。

验证：

1. npm run build。
2. 联调测试：编辑顶点、画一个合法面、改类别、删除面、保存、刷新、导出。
3. 双会话测试：A 保存后 B 以旧版本保存，B 收到 409 并可刷新。
4. 非法类别/几何返回 422，前端显示后端信息。

完成标准：

- 从编辑到导出的完整闭环不依赖手工拷贝文件。
- 冲突、权限不足、校验失败都不会静默丢失或覆盖用户成果。

### 任务 8：部署、离线镜像和回归验收

**负责人：** 后端负责人主办，前端负责人配合。  
**依赖：** 任务 5 至 7。  
**涉及文件：** docker/Dockerfile.inference-gpu、docker-compose.prod.yml、前端镜像构建文件、部署说明和回归记录。

先写/更新验证项：

1. GPU worker 镜像构建后能够 import 新模型、矢量化和项目成果服务模块。
2. 前端生产镜像包含新增 npm 依赖；不能依赖仅在宿主机挂载的 node_modules。
3. 离线部署启动后，后端、worker、前端的版本与 lockfile/tag 一致。
4. 项目成员在部署环境读取受控瓦片；非成员不能读取。
5. 无 GPU 或未挂模型权重时，已有失败提示保持清晰，部署不把错误伪装为“无矢量”。

实现：

1. 审核 inference GPU Dockerfile 的显式 COPY 列表，加入本功能真正需要的模块；不以整库 COPY 掩盖遗漏。
2. 因 frontend Compose 目前不挂载 package.json/package-lock，必须重建并发布包含已锁定依赖的前端镜像。
3. 编辑器优先走后端 api_tile_url。对于现有 /tiles/ 兼容路径，若生产静态代理仍保留，则配置 PROXY_TILES_TARGET 指向 miner-api 并完成访问测试；不能默认浏览器能直接访问 Miner 私有路径。
4. 编写部署前检查：磁盘空间、模型权重挂载、地图瓦片目录、数据库备份、镜像版本、端口和权限。
5. 保持模型权重、镜像 tar、影像瓦片和运行 volumes 在制品/挂载目录，不提交到源代码仓库。
6. 记录一次升级和回退步骤。回退仅停用新入口/回滚应用镜像；新成果数据保留，不执行破坏性删除。

验证：

~~~text
docker compose -f docker-compose.prod.yml config
docker compose -f docker-compose.prod.yml up -d
~~~

随后执行一次真实小样本：项目推理 -> PNG 保持可见 -> 自动矢量 ready -> GeoView 编辑保存 -> GeoJSON 下载；并运行第 1 至 7 任务全部回归测试。

完成标准：

- 在与甲方相同或更弱的离线部署条件下完成小样本全流程。
- 部署文档能由另一位开发者按步骤复现，无需口头补充。
- 现有推理、PNG 浏览、项目历史和权限测试均未回归。

---

## 6. 两人协作、分支和 PR 规则

### 6.1 角色分工

| 角色 | 连续负责模块 | 可并行开始的工作 | 不负责内容 |
| --- | --- | --- | --- |
| A：后端/GIS/部署 | 任务 0-4、8 | 标签 TIFF、矢量化、模型/API、受控瓦片、Docker | Vue 页面样式、地图交互实现 |
| B：GeoView 前端 | 任务 5-7 | route/context/API mock、页面骨架、地图组件 | 数据库结构、几何服务端校验、Docker 共享文件最终修改 |

协作采用“契约先行 + fixture 并行 + 真实接口联调”的方式。B 不等待全部后端完成：在任务 0 合并后按 fixture 开发只读页面；A 在任务 2 完成后提供实际读取响应；B 在任务 4 完成后接保存接口。

### 6.2 分支和提交要求

前提是先解决任务 0 中的统一 Git 基线问题。之后采用：

- 分支格式：feature/vector-label、feature/vector-result-api、feature/geoview-vector-read、feature/geoview-vector-edit、release/vector-editor-v1。
- 每个分支只服务一个 PR 目标；不要在同一 PR 混入模型文件、数据、镜像 tar、package 大升级或不相关格式化。
- 提交信息使用中文且描述用户价值，例如：新增分类成果自动矢量化基线。
- PR 描述必须包含：需求对应项、影响文件、接口/数据变更、验证命令与实际结果、截图或请求样例、回滚方式、已知限制。
- PR 只在 CI/本地最小验证通过后请求评审；测试暂不能运行时必须写清环境阻塞和风险，不能标记为已验证。
- 开发者不自行 merge 自己的 PR；另一人至少核查契约、权限、错误处理、测试覆盖和不相关变更。
- 新增数据库表、Dockerfile、Compose、路由、锁文件属于高风险变更，必须在 PR 描述中单列影响和回退方案。
- 任何接口字段或分类字典变更必须先修改契约，并在同一 PR 中更新前后端测试。

### 6.3 建议 PR 顺序

| PR | 内容 | 负责人 | 依赖 | 合并门槛 |
| --- | --- | --- | --- | --- |
| PR-0 | 契约、fixture、Git/部署基线说明 | A 主办，B 评审 | 无 | 两人书面确认接口与范围。 |
| PR-1 | label GeoTIFF 与空间校验 | A | PR-0 | 空间 bounds/CRS 测试与旧管线回归。 |
| PR-2 | 自动矢量、成果模型、Revision 0、读取 API | A | PR-1 | 后端单测、权限测试、fixture GeoJSON。 |
| PR-3 | 修订保存、审计和服务端导出 | A | PR-2 | 409/422/权限/审计测试。 |
| PR-4 | GeoView 路由、API 层、只读地图和入口 | B | PR-0；真实联调依赖 PR-2 | 前端 build、fixture 和读取接口联调。 |
| PR-5 | GeoView 编辑、保存冲突和导出 | B | PR-3、PR-4 | 编辑闭环与双会话冲突测试。 |
| PR-6 | Docker、离线部署、端到端回归 | A 主办，B 验收 | PR-1 至 PR-5 | 部署演练、小样本 E2E、回退记录。 |

### 6.4 每日联调节奏

1. 每天开始前在任务板同步：正在做的任务号、接口变更、是否阻塞。
2. 契约字段变动必须先在 PR-0 文档或其后续契约 PR 中提出，不能口头变更。
3. B 用约定 fixture 进行页面开发；A 在接口可用时给出脱敏响应样例和测试项目 ID。
4. 每次 PR 合并后，另一人拉取同一 commit 完成一条最小验收路径并记录结果。
5. 共享文件冲突时，由唯一负责人整合；另一人通过 review 提建议，不直接并行修改该热点文件。

---

## 7. 验收清单与工期估算

### 7.1 逐项验收

| 验收项 | 通过标准 |
| --- | --- |
| 空间正确性 | 同一矿山标签 TIFF 的 CRS、bounds 与裁剪 TIFF 对齐；叠加底图不发生整体偏移。 |
| 自动矢量化 | 类别、ROI 裁剪、16 像素过滤、EPSG:4326 和属性字段符合契约。 |
| 兼容性 | 原 PNG 预览、历史、下载、推理和现有项目权限流程不回归。 |
| 权限性 | 非项目成员不能获取成果、修订、导出或受控瓦片。 |
| 编辑可用性 | 有权限者可修改/画/删/改类别/保存；只读者不可写。 |
| 冲突安全 | 旧版本保存返回 409，无静默覆盖。 |
| 可追溯性 | 每份成果能追到项目、矿山、年份、推理任务、模型、自动基线和每个编辑版本。 |
| 部署可复现 | 离线镜像与挂载路径明确，小样本 E2E 能复现。 |

### 7.2 两人并行工期

以下是已有代码基础上、不含甲方数据清洗、模型重训和大底图上传能力的估算：

| 阶段 | 人日 | 日历时间（两人并行） | 主要风险 |
| --- | ---: | ---: | --- |
| 契约/fixture/Git 基线 | 1-2 | 1-2 天 | 需要确认统一 PR 承载仓库。 |
| 后端标签/矢量/模型/API | 7-9 | 7-9 天 | 空间 transform、ROI 边界、历史兼容。 |
| 前端只读/编辑/保存 | 5-7 | 可与后端后半段并行，约 5-7 天 | MapLibre worker、瓦片鉴权、交互细节。 |
| 部署/E2E/缺陷修正 | 3-5 | 3-5 天 | 离线镜像、真实样本性能和权限。 |

**建议承诺：** 开发完成及内部验收约 12-16 个工作日；若甲方现场环境、真实底图、GPU/离线镜像或权限策略与现状差异较大，再预留 3-5 个工作日的部署适配缓冲。不要承诺“大底图单终端切片”和“在线训练”包含在此周期内。

---

## 8. 实施前需要你审核的决策

1. 是否认可 v1 只导出 GeoJSON，KML/Shapefile 暂缓？
2. 是否认可默认过滤小于 16 分类像素的碎斑？若验收有面积阈值，应由甲方以平方米/亩给出后转换为按影像分辨率计算的规则。
3. 是否认可 v1 编辑操作仅限改顶点、绘制、删除和改类别，不做实时协同/自动合并/拓扑修复？
4. 是否同意由后台管理员离线放置并登记大底图，本轮不做浏览器大文件上传或断点续传？
5. 请明确统一 Git/PR 的承载仓库或授权方案。没有此决定，代码能做，但“两人 PR 审核、分支和可回滚协作”无法真正执行。
6. 是否同意上线前用一份真实但可脱敏的小矿山样本进行空间位置和编辑闭环验收？

审核确认后，将严格按 PR-0 至 PR-6 顺序实施；如第 8 节任一决策修改，我会先更新契约和计划，再开始相应代码。
