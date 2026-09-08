# 推理 Worker 常驻韧性优化设计（参考江西 EPIPE 修复）

> 状态：实施中。本文档是该任务的 PR 记录：写明背景、跨项目对照结论、输入输出、修改模块与验证方案。

## 1. 背景

江西项目（`D:\项目\JiangXi\JiangXi-Platform`，分支 `fix/inference-worker-epipe`）于 2026-09-07 修复了推理 worker 的常驻性缺陷：GPU 常驻进程的 socket 服务循环对健康检查遗留的陈旧连接写响应时触发 `BrokenPipeError`，异常逃出 serve 循环杀死进程，entrypoint 的 `wait -n` 再放大为整机退出——表现为「每次启动只能完成一次推理，第二次推理必崩」。江西的修复分三层：

1. `mmseg_worker.py`：serve 循环内 `_send_message` 独立 try/except，单连接死亡不得终止常驻服务；
2. `docker/entrypoint.sh`：worker 改为带 TERM 转发的自动重启监督循环（2 秒拉起）；
3. `kml_roi/pipeline.py`：逐阶段计时 `stage_durations` 随 summary 返回，为批处理/并行化立项提供数据。

配套流程规范：AGENTS.md §5.1「连续运行验证」——任何执行一次型的验证必须补齐「重复执行 → 混合小批次 → 全量回归」三级，单次成功不算通过。

## 2. 云南对照结论（移植什么、不移植什么、为什么）

| 江西改动 | 云南现状 | 结论 |
| --- | --- | --- |
| socket serve 循环的单连接隔离 | 云南无 socket 版常驻 worker（`interface/` 下无 `mmseg_worker.py`，模型由推理 worker 进程内加载） | **机制不移植**；但同缺陷类别在云南存在另一形态：`InferenceWorker.run_forever` 的循环体 `self.run_job(job)` 无 try/except，且 `run_job` 在 try 块之前有一段无保护序（`json.loads` 载荷解析、`work_dir.mkdir(exist_ok=False)`）——坏载荷或残留运行目录会让异常逃出循环杀死 GPU worker 进程，效果同样是「一次崩溃后所有排队任务停摆」。**移植缺陷类别的修复**：进程内任务隔离 |
| entrypoint 监督循环（`wait -n` 放大问题） | 云南推理 worker 是独立 compose 容器（`start-inference-worker.sh` 末尾 `exec`，进程即容器），脚本内无多服务 `wait -n`，且 `runtime-base` 已有 `restart: unless-stopped` | **不移植**。进程退出已由容器级重启兜底；监督循环在云南无增量收益。代价记录：容器重启需重跑 wait-for-mysql、运行时检查与模型加载（约数分钟），这正是要做进程内隔离的原因——让容器根本不退出 |
| KML-ROI `stage_durations` | 云南 pipeline 同源但更演化（进度/取消/超时/CPU 回退），无计时 | **移植**，按云南实际阶段（prep/kml_load/bounds_filter/tiles/inference/distribute）插桩，经 `finish()` 闭包统一附着到所有出口的 summary |
| §5.1 连续运行验证 | 云南无对应规范 | **移植为流程**：写入 `docs/testing-playbook.md`（技巧 11），本任务验证自身按三级执行 |

## 3. 输入输出

**修改对象（输入）**：`InferenceWorker.run_forever/run_job` 的任务循环；`run_kml_roi_pipeline` 的执行路径。

**输出（行为变化）**：

- 单个任务在流水线 try 之外失败（载荷非法、运行目录冲突）或任何未预期异常时：该任务被标记 `failed`（带明确 `error_code`），worker 进程与后续任务不受影响；日志保留完整堆栈，不静默吞错。
- 新增错误码：`PAYLOAD_INVALID`（`request_payload_json` 非法 JSON）、`WORKDIR_CONFLICT`（运行目录已存在，保守拒绝、不清理，避免误伤活跃任务）、`WORKER_ISOLATED`（run_forever 层兜底隔离，正常情况不应出现）。
- 每个任务的 summary 新增 `stage_durations`（各阶段耗时，秒，round 3 位）与 `total_seconds`，随 `finish_job(result=...)` 落入任务 `result_json`，供容量评估与性能归因。

**兼容性**：不改 HTTP API、不改任务状态机取值、不改数据库结构；`stage_durations`/`total_seconds` 为 result_json 新增键（消费方忽略未知键即可）；既有错误码与 finish 路径不变。

## 4. 修改模块

| 模块 | 改动 | 职责边界 |
| --- | --- | --- |
| `backend/applications/inference/worker.py` | 主模块：run_forever 任务级隔离；run_job 无保护序收进受控失败路径 | 只做进程韧性，不碰流水线算法与发布逻辑 |
| `backend/applications/kml_roi/pipeline.py` | 阶段计时插桩 | 只测量不改行为 |
| `backend/test_inference_runner.py` | 回归测试：毒载荷隔离、运行目录冲突、同 worker 连续双任务；顺带修复 `main` 上已红的 stale mock（publisher mock 补 `classification_results`，对齐 `72d5764` 后的生产契约） | 测试与被测同文件演进 |
| `backend/test_kml_roi_pipeline.py` | 断言 `stage_durations`/`total_seconds` | — |
| `docs/superpowers/specs/`（本文档） | PR 记录 | — |
| `docs/testing-playbook.md` | 技巧 11：连续运行验证 | — |

**不修改**：推理 API 路由、`jobs.py` 队列语义、发布/矢量化链路、空间 worker（同类韧性问题留作后续任务）、compose 编排。

## 5. 验证方案

固定环境：`yunnan-runtime:current` 镜像挂载工作树，`conda activate MMSeg310` 后在 `/app/backend` 执行（命令见开发规范第 7 节）。

1. **红灯先行**：新增测试在隔离逻辑实现前先失败于「异常逃出 run_forever」。
2. **递进**：单文件（`test_inference_runner`、`test_kml_roi_pipeline`）→ 推理族小批量（`test_inference_jobs`、`test_inference_worker_app`、`test_project_inference_results`）→ 全量 `discover` 对照已知失败台账（2026-09-07 基线：2 例 CRLF 伪失败、1 例 mmseg 发现失败、10 条件跳过）。
3. **连续运行验证（三级，按江西 §5.1）**：① 重复执行——同一 worker 实例连续处理两个任务（进程内回归测试覆盖）；② 混合小批次——毒载荷 + 正常任务 + 目录冲突混合串行；③ 全量回归。真实 GPU/CPU 端到端双跑受运行资产限制另行安排，如实记录执行范围。
