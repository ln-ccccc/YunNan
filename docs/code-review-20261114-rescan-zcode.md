# 独立复扫报告——35 项之外的漏网问题（ZCode，2026-11-14）

> **背景**：应用 [操作链状态一致性镜头](code-review/methodology.md)（2026-11-14 入册）对 Astra 35 项清单（见 [code-review-20261114-astra-35.md](code-review-20261114-astra-35.md)）做独立复扫，找清单之外的漏网。四路并行子代理（GeoView 链/推理成果链/未提交新接口/fixture 可达性）+ 主控逐条亲验 P2 级发现。
> **验证边界**：无 Docker/MySQL/浏览器环境；"实锤"=主控亲自对到行号与数据流；"待复核"=子代理给出双侧行号、主控未逐行复核。与既有 35 项无重复（排除清单已下发给各路）。

## 主控亲验实锤（10 条）

### R1【P1·卫生/泄密风险】7.8GB 生产备份目录未入 .gitignore，`git add -A` 即入库
- **位置**：`backups/yunnan_backup_20261106_150646/`（508MB MySQL 全量 dump 含账号口令哈希 + 4.4GB project_storage 原始影像）；`.gitignore:7/51` 只忽略 `volume_backups_before_restore_*/` 与 `.migration-backups/`；`git check-ignore` 退出码 1（未忽略）。`?? .claude/` 同类。
- **影响**：任何人一次 `git add -A && git commit && push` 就把数据库 dump 与原始影像推进版本库，直接违反 AGENTS.md §8"不提交运行数据/原始影像"，且一旦推远端极难彻底清除。
- **建议**：.gitignore 立即补 `backups/` 与 `.claude/`；若备份目录需保留在仓库路径下，移到仓库外或用 `.gitkeep`+子目录忽略。
- **验收**：`git check-ignore backups .claude` 均命中；`git status` 不再出现两类 untracked。

### R2【P2·代码确认+条件风险】发布回滚第一步失败会毁掉既有年份成果且不可恢复
- **位置**：`backend/applications/project_hub/inference_results.py:248-268`。
- **问题**：`except` 分支先 `if target_dir.exists(): shutil.rmtree(target_dir)`（:261-262），再从 backup 还原（:263-264）。若失败发生在**第一步** `os.replace(target_dir, directory_backup)`（:249）——此时 target_dir 仍是旧内容、backup 不存在——旧成果被 rmtree 删除且无备份可还原；随后 `summary_target.unlink()`（:265-266）把旧 change_matrix 汇总也删掉（summary_backup 尚未创建）。
- **触发面**：Linux 生产上目录 rename 不受打开句柄影响，触发依赖罕见 IO 错误；Windows 开发环境有打开句柄即触发。**条件风险、后果不可恢复**。
- **建议**：except 分支按"哪一步已成功"分档清理：仅当 `publication_dir→target_dir` 已成功（新内容已就位）才允许 rmtree target_dir；第一步失败时直接 raise 不动任何现有文件。
- **验收**：注入第一步 os.replace 失败，断言旧成果目录与旧 summary 完好；注入 commit 失败，断言新内容清除、旧内容还原。

### R3【P2·代码确认】多 FID 发布半途失败：部分成果已落库可见，任务终态却是 failed、无发布审计
- **位置**：`inference_results.py:211-274`（循环内逐 FID 搬目录+`db.session.commit()` :258；后续 FID 在 :214/:165/:167 抛错直接冒泡）+ `backend/applications/inference/worker.py:508-562`（捕获后整任务置 failed，只记 `inference_failed` 审计；循环后的 `inference_result_published` 审计 :337-359 不写）。
- **影响**：终态 failed 与"部分成果已发布、资产已 ready"并存，历史页/资产页与任务列表对账矛盾；`.{fid}.publish-<hex>` 暂存目录在 pre-try 抛错路径残留。
- **建议**：逐 FID try/except 收集失败项，任务级终态区分"全部失败/部分成功"；部分成功时发布审计按实际成功 FID 记录；失败 FID 的暂存目录清扫。
- **验收**：565 多矿山任务注入中间某 FID 矩阵生成失败：成功 FID 成果可见+审计在，失败 FID 有明确失败记录，任务终态与可查状态一致。

### R4【P2·代码确认，窗口毫秒级】取消 queued 任务无 CAS，与 worker 领取竞态
- **位置**：`backend/applications/inference/jobs.py:298-304`——`if job.status == "queued": job.status="cancelled"; commit`，无 `WHERE status='queued'` 条件更新守卫。
- **影响**：与 `claim_next_job` 的 CAS（:319）竞态时，已 running 的任务被 API 覆写为 cancelled 且 worker 继续执行；收尾 `finish_job` 用内存陈旧 status 校验转换，或 cancelled→succeeded 覆盖（发布照常、用户已收到取消成功），或抛转换异常使任务停在不一致终态（`status.py:4-7` 无 cancelled 出边）。同族：发布前取消复查（worker.py:468）与 finish 之间落地取消 → succeeded+cancel_requested=True。
- **建议**：queued 取消改条件更新（`UPDATE ... WHERE status='queued'` 检查行数）；finish_job 前以 DB 当前值+行锁判定终态，cancelled 为终态无出边。
- **验收**：并发取消与领取的压力用例：任一时刻终态唯一且合法；取消成功响应的任务绝不变 succeeded。

### R5【P2·代码确认】同名同年重传覆盖共享项目输入，排队旧任务消费新影像
- **位置**：`backend/applications/inference/interpretation.py:56-74`——`_publish_project_input` 按**文件名** `os.replace` 到 `projects/<id>/inputs/interpretation/<year>/<name>`；:96-97 任务载荷存该路径；`worker.py:343-350` 运行时才读文件。
- **影响**：修正影像后同名重提交 → 排队中的旧任务实际推理**新文件**，成果却归属旧 job_id；输入快照与任务身份脱钩。"重传同名修正"是合理用户流程，非构造场景。
- **建议**：项目输入按 job 维度隔离（`inputs/interpretation/<year>/<job_id>/<name>`）或内容寻址（哈希键）；至少发布时若 mtime/大小与入队时不符则拒绝并标记。
- **验收**：任务A排队（同名文件）→重传新版本→立即提交任务B→A完成后断言A成果输入指纹=A入队时文件。

### R6【P2·实锤】GeoView 保存成功但版本列表刷新失败被报"保存失败"，诱导重复修订
- **位置**：`frontend/src/views/mainfun/ClassificationResultEditor.vue:744`（及 :736 同型）——`await this.loadRevisions()` 在 saveDrawing 的 try 内；catch（:746-755）统一 `this.$message.error('保存分类成果失败')`。
- **影响**：保存请求已成功、服务端已建新修订、本地修订号已更新（:722），仅紧随的 revisions 请求失败 → 用户看到"保存失败"再点保存 → 以新基线再提交内容相同的新修订（修订不可变只增不减），版本记录被污染。
- **建议**：loadRevisions 移出 try 或单独 catch，失败提示"已保存为 Vx，版本列表刷新失败请手动刷新"。
- **验收**：保存成功+revisions 请求 mock 5xx：出现成功提示（或明确的"已保存"提示），无"保存失败"文案，修订数不因重试+1。

### R7【P2·实锤】项目模式"一键清空历史"是跨上下文的破坏性空操作
- **位置**：`frontend/src/views/mainfun/Segmentation.vue:264-272`（按钮无条件渲染，无项目模式隐藏）+ :435-445（clearCurrentHistory）；数据源 `frontend/src/utils/getUploadImg.js:24`（带 projectId）；后端 `backend/applications/api/analysis.py:479-492`（clear 只删 `_iter_flash_records()` 的 flash PNG，项目记录不动）+ :420-438（项目模式列表来自 `projects/<id>/outputs/...`）。
- **影响**：项目上下文打开该页 → 点"一键清空历史" → 提示"清理完成"但可见列表零变化；同时把无项目入口页展示的全局 flash 历史**真删了**（破坏性副作用落在另一个上下文）。
- **建议**：项目模式隐藏该按钮，或 clear 语义按当前上下文分流（项目模式清项目推理历史需后端支持+二次确认文案列明将删什么）。
- **验收**：项目模式点清空：项目记录不受影响、flash 历史不被误删（或按新语义明确删除范围）。

### R8【P2·实锤】项目模式"图像增强/降噪"选项对实际推理静默失效
- **位置**：`frontend/src/views/mainfun/Segmentation.vue:58-130`（增强/降噪 UI 无条件展示）+ `utils/preHandle.js`（勾选写 `uploadSrc.prehandle`）；但主链 `utils/getUploadImg.js:243-249` 的 `kmlRoiInfer` 载荷只有 `{old_tif_path,new_tif_path,year,device,project_id}`，分片 meta（:166-170 区域）同样不含 prehandle；后端仅 `/semantic_segmentation`（standalone 回退路径，analysis.py:212/231）消费该字段。
- **影响**：带 project_id 推理用未增强原始影像，与用户所见预览/所选不一致，结果偏离预期且无提示。
- **建议**：要么把 prehandle 传入项目推理链（后端消费），要么项目模式禁用增强选项并注明"项目推理不应用预处理"。
- **验收**：项目模式勾选 CLAHE 推理：后端收到的输入带增强，或前端明确提示不适用。

### R9【P2·实锤】undelete 异常回显含绝对物理路径
- **位置**：`backend/applications/project_hub/service.py` undelete_project 的 `shutil.move` 失败抛 `FileNotFoundError(str 含 'D:\...\trash\12_...')` → `backend/applications/api/error_responses.py:14` `BUSINESS_EXCEPTIONS=(ValueError, FileNotFoundError)` + :24 直接回显 `str(error)`。
- **触发**：并发重复 undelete（两请求都过 `project_root.exists()` 检查后竞争 move）或 trash 目录被运维清理。需登录。
- **影响**：泄露 PROJECT_STORAGE_ROOT 布局（属 35 项 #21 家族的新具体出口）。
- **建议**：undelete 捕获 move 异常转译为领域消息；`FileNotFoundError` 不应整类视为业务异常（原生异常统一日志+通用文案）。
- **验收**：并发双 undelete / 预删 trash 目录：响应无绝对路径。

### R10【P2·实锤·fixture 可达性】删除守卫测试用生产不存在的 dataset_kind
- **位置**：`backend/test_project_api.py:300-313` fixture 写 `dataset_kind="classification_dir"` 并断言 409；生产成果登记实际写 `dataset_kind="inference_result"`（`inference_results.py:91/99`），全仓无任何写入点产出 `"classification_dir"`。
- **影响**：当前行为恰好相同（非 imagery 均 409），但测试证明的是生产不可达状态；生产真实 kind（inference_result）从未被任何测试送入删除接口。若守卫将来收窄为精确匹配/白名单，测试保持全绿而生产成果登记行可经数据集通道删除——恰是 .active 先例（35 项 #11）的同型隐患。
- **建议**：fixture 改用生产真实值 `inference_result`；补一条"生产 kinds 枚举与测试覆盖对账"的守卫测试。
- **验收**：grep 全仓 dataset_kind 写入点与测试 fixture 值一致；409 断言用真实 kind。

## 子代理报告、待主控/负责人复核（按可信度排序）

1. **【P2】引用检查全表扫 + 双实现口径不一**（service.py delete_dataset vs snapshots.py:281-292）：`InferenceJob.request_payload_json.like('%"dataset_id"%')` 无界 `.all()` 跨项目全表扫、随任务数线性增长（违反 AGENTS.md §10）；一处用裸 `json.loads`（payload 损坏→500）、一处容错（静默无引用），注释却称同口径。
2. **【P3】CUDA OOM 回退后 CPU 模型永驻**（worker.py:315/:380/:576-590）：仅 HOST_MEMORY_EXHAUSTED 分支任务后卸载重探 GPU，OOM 分支不卸载——单次大任务 OOM 后所有后续任务永久 CPU 数十倍慢，且 fallback_reason 误标（任务自身从未尝试 GPU）。
3. **【P3】发布/矢量化段不受死线与取消约束**（worker.py:256-261 死线仅在瓦片环；:468 复查到 finish 间不查取消）：565 矿山级任务发布段可远超任务时限无干预。
4. **【P3】项目指数文件读-改-写无互斥**（inference_results.py:426-449 `upsert_project_index_results`）：gunicorn 多线程下并发 `/spectral_indices` 同 fid 不同指数后写者覆盖前写者，年度值静默丢失。
5. **【P3】历史列表 stat() 与发布目录交换竞态→500**（api/analysis.py:143/105 对 glob 结果逐个 stat，发布 os.replace 瞬间 FileNotFoundError 未捕获，整列表 500）。
6. **【P3】分类历史元数据回退旧 result_id**（inference_results.py:320-332 linkage 失败仅告警；analysis.py:149-179 历史条目取该 linkage）：失败后历史页新影像指向上一任务成果，编辑入口错位。
7. **【P3】loadRevisions 无过期守卫**（ClassificationResultEditor.vue:776-780 无 reloadSeq 校验，对比 :346/:720 有）：响应乱序时 B 成果版本面板显示 A 的修订列表。
8. **【P3】后台推理完成清空 fileList 抹掉用户新选批次**（getUploadImg.js:338 IIFE 尾部 `this.fileList=[]`，运行可达数分钟，期间合法新选批次被静默清空）。
9. **【P3】浏览器前进/后退切成果：取消丢弃后 URL 与内容脱节**（ClassificationResultEditor.vue:277-281 watch fullPath 触发在导航完成后，无 beforeRouteUpdate）。
10. **【P3】裁剪链路丢年份参数且先关弹窗清文件**（MyVueCropper.vue:209-214 载荷无 year/project_id；:240 emit 时机早于上传结果→父级立即清 fileList）。
11. **【P3】结果图下载失败零 UI 反馈**（utils/download.js:36-39 仅 console.error）。
12. **【P3】快照导入不校验 dataset_kind 枚举与标量类型**（snapshots.py `_validate_import_manifest` 只查存在性；任意 kind 落库绕过 imagery-only 限制，非整型 fid/year 严格模式下 500）。
13. **【P3】undelete 崩溃窗口产生不可自愈半恢复态**（move 成功后崩溃→目录回 projects/ 而 DB 仍软删→此后 undelete 永久 409，须人工移目录）。
14. **【P3】dataset delete 并发双删审计重复**（并发时双 `dataset_deleted` 活动日志，无数据损坏）。
15. **【低危注记·fixture】**：`retired` 状态生产无写入点（test_classification_result_api.py:774）；瓦片假 PNG、目录命名、revision-0 布局、备份状态组合、export file_path 等 6 处 fixture 与生产形态不一致但当前不影响被测行为（详见子代理报告，修复 R10 时顺带对账）。

## 排除项（追过、验证无问题或属已知）

GeoView 编辑器保存竞态主体（代次+快照比对+markSaved+migrateIds）、409/422 分支、上传取消/断点续传、waitForInferenceJob 收口、undelete/dataset-delete/retry 鉴权（均有 login_required，单管理员模型）、路径遍历（resolve_storage_path/resolve 强制 containment 全覆盖）、同 FID 并发发布互删（单 worker 串行不可达）、claim 空转（已修复的 rollback）、2026-11-05 迁移审计已修项、Astra 35 项本身。

## 交接说明

- 本报告只识别问题，未修改任何代码；修复与提交按分工交 Claude Code，建议并入 [code-review-20261114-astra-35.md](code-review-20261114-astra-35.md) 台账统一排优先级（R1 建议立即处理）。
- 待复核 15 条中 1-6 与 Astra 清单第 8/26/27 项同域，可同批修复；7-11 属 GeoView 前端，与 Astra 清单第 15-17 项同域。
