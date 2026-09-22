# Miner 原始影像溯源标签 2026-09-22

## 需求

Miner 矿山详情弹窗中，与"地物分类"同级新增"原始影像"标签页，列出该矿山参与过的地物分类推理所用的原始影像（文件名/年份/大小/推理时间/任务状态），支持下载，便于溯源。

## 实现

- **后端**（`project_map.py` + `api/project.py`）：数据源是 `InferenceJob.request_payload_json`（归一化后的 `new_tif_path` 指向项目 `inputs/interpretation/<year>/` 中的拷贝，建任务时即落盘）。列表按矿山过滤、(输入,年份) 去重、存在性标注，**物理路径不出现在 DTO**；下载按任务记录解析并强制位于本项目 inputs 根内（路径只来自 DB，客户端不可指定）。端点：`GET /api/projects/<id>/mines/original-imagery?fid=` 与 `.../<job_id>/download`。
- **BFF**（`miner/routes/projects.js` + `services/projectBackend.js`）：列表走通用 relay，下载走 relayBinary（jobId UUID 校验复用既有参数校验器，下载超时 10 分钟覆盖 GB 级文件）。
- **UI**（`MineDetailModal.vue` + `useMineData.js` + `mineDetailPresentation.js`）：标签数组追加 `Original`（中文"原始影像"）；与变化矩阵同竞态门控随详情一并拉取；下载经 blob 落盘（上游 JSON 错误体给出提示）。文件缺失的行保留但不可下载（溯源仍可见"用过哪个输入"）。

## 验证

- 后端容器全量 **391 passed / 0 failed**（新增 9 例：过滤/去重/缺文件标注/越根路径剔除/fid 归属/下载流+attachment/未知任务/跨项目隔离/鉴权）。
- miner `node --test` **57/57**（新增 presentation 3 例 + 路由 3 例）+ vite build 零错误。
- GUI 实测（真实栈，DOM 注入级）：登录→项目→地图→搜索 713→详情弹窗→标签页含"原始影像"（与地物分类同级）→列表 7 条（含当日 572MB 分片上传输入与已取消任务输入，状态如实展示）→下载按钮触发 BFF 流式下载成功。
