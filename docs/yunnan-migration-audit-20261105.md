# 云南项目迁移检查报告 —— 江西问题地雷图全量对照（2026-11-05）

> **方法**：对照《云南项目检查清单——江西项目问题迁移》（docs/yunnan-migration-checklist-20261104.md），按江西工作法执行：主控谱系对账（§1/§6）+ 3 个并行子代理（后端 §2/§4、前端 §3、安全 §5）+ 主控对全部 P1 与抽样 P2 逐条亲验。
> **性质**：只读检查，本轮未修改任何代码。修复优先级待负责人排序后批量执行。
> **检查环境**：主树 main@313efdb；现役 7 容器（mysql/backend/frontend/miner-api/miner-web/inference-worker/spatial-worker，均 Up，代码 bind 主树模式）。

---

## 0. 总览

| 风险域（清单章节） | 判定 | P1 | P2 | 关键结论 |
|---|---|---|---|---|
| §1 部署/镜像/数据谱系 | 部分命中 | 2 | 4 | worker 全在跑、无数据混线；但镜像内 dist 落后 5 个月 + 启动重建模式 |
| §2 API 缓存/超时/异步 | 部分命中 | 1 | 3 | 推理/切片已 Job 化；semantic_segmentation 等同步遗留 |
| §3 前端运行时 | 大面积已防护 | 1 | 2 | 江西两起白屏 P0 模式全量扫描不存在；主通道无超时+遮罩无逃生口 |
| §4 worker 生命周期 | 主体已防护 | 1 | 2 | EPIPE/取消状态机/孤儿回收均为修复态；spatial 取消不杀进程组 |
| §5 安全 | 部分命中 | 1 | 5 | 分片上传通道绕过白名单（可存储型 XSS）；其余四件套主体已防护 |

**P1 合计 6 条，P2 合计 16 条，P3/卫生 8 条。**

---

## 1. P1 清单（送测/日常使用会真实触发）

### P1-1【安全】分片上传通道绕过扩展名白名单 → 存储型 XSS
- **江西对应**：5.1（SVG 允许集）+ 5.6（魔数校验）叠加
- **证据**：
  - `backend/applications/api/file.py:154-159` —— 分片 complete 只黑名单 `dat/bin/hdr`，无扩展名白名单，`dest_path = upload_root / f"{uuid.uuid4()}{suffix}"` 任意后缀可落盘
  - `backend/applications/common/utils/upload.py:45-51` —— `upload_one_from_path` 跳过 `photos.save` 的白名单校验（注释自认）
  - `backend/applications/extensions/flask_uploads.py:472-480` —— `send_from_directory` 按扩展名推 Content-Type，`.svg` → `image/svg+xml` 内联提供
  - 单发通道白名单无 svg（`flask_uploads.py:42-45` 注释明示已防）；回归测试 `test_security_recheck.py:249-272` 只测单发
- **利用链**：登录 → init(filename=x.svg) → chunk → complete → `GET /_uploads/photos/<uuid>.svg` 得内联 SVG（需会话，认证内 XSS）
- **建议**：complete 前补扩展名白名单（与 IMAGES_WITH_TIFF 对齐）+ 可选魔数校验；`test_svg_upload_rejected` 补分片通道用例

### P1-2【后端】同步长推理端点占满 gthread + 孤儿 GPU 进程
- **江西对应**：2.3（长任务异步化）
- **证据**：
  - `backend/applications/interface/mmseg_inference_caller.py:106-113` —— `subprocess.run(cmd, timeout=1200)` 在请求线程内同步跑
  - 前端活跃调用：`frontend/src/components/MyVueCropper.vue:210`、`frontend/src/utils/getUploadImg.js:273,353`
  - 实际 gunicorn `--timeout 1800`（compose 注入 `WEB_TIMEOUT_SECONDS:-1800`），2 workers × 4 threads
- **影响**：8 个并发该端点即占满 backend 全部线程（连 healthcheck 都可能饿死）；请求最长挂 30 分钟；>1800s 时 worker 被 SIGKILL → subprocess 孤儿继续占 GPU
- **同类遗留**（P2 级）：image_pre / spectral_indices / imagery clip / imagery slice 同步（clip/slice 有 MAX_SLICE_LIMIT=256 与 2GB 护栏）
- **建议**：按 AGENTS Job 模式异步化（submit/轮询/取消），或至少 BFF 层限流

### P1-3【后端】spatial 切片取消不杀进程组 → gdal2tiles 孤儿
- **江西对应**：4.4（取消语义）
- **证据**：`backend/applications/project_hub/spatial_worker.py:193-199`（`_terminate` 仅 `process.terminate()` 单 PID）、`:208`（`--processes=4`）、`:224`（`Popen` 无 `start_new_session`/进程组）
- **影响**：大底图切片中取消 → job 收口 cancelled，但 gdal2tiles 的 multiprocessing 池子进程孤儿化，继续占 CPU/写盘
- **建议**：`start_new_session=True` + `os.killpg`；补真实 e2e 取消测试（江西教训：单测全 mock 抓不住这类）

### P1-4【前端】GeoView 主通道无 timeout + 全屏遮罩无逃生口
- **江西对应**：2.2（无超时死锁组合）
- **证据**：`frontend/src/api/request.js:35-39`（`axios.create` 无 timeout）；`frontend/src/utils/loading.js:9`（`lock:true`，全文 35 行无自动解除）；唯一超时在 `api/auth.js:10`（10s，session 已豁免）
- **影响**：后端 worker 被长请求占满时，任一非 silent 请求（history/analysis 等）pending → 全屏遮罩永锁、整站不可操作
- **对照**：miner 前端与 BFF 侧已防护（AbortSignal.timeout 分层 5s/15s/30s/300s/600s，无全局遮罩）
- **建议**：request.js 加默认 timeout（30-60s）或 loading 加超时自动解除（江西用 15s 逃生口）

### P1-5【部署】镜像内前端 dist 落后主树 5 个月 —— **2026-11-05 修复批中降级为 P2（观察项）**
- **江西对应**：1.2/1.3（可写层资产 vs 镜像固化、谱系分叉）
- **原判定的修正**（修复时亲验推翻）：
  - `yunnan-frontend` 容器**整目录 bind 主树 frontend/**（含 src/node_modules/dist）——镜像内 dist 被遮蔽、运行时无效，"送测即落后 5 个月"不成立于该容器；架构定案（offline_deployment_guide.md:127）本就是"镜像=不可变运行时基座（Python/CUDA/MMCV），业务代码 bind 主树"，且镜像无 Dockerfile、不现场重建
  - `yunnan-miner-web` 是"镜像 node_modules + 启动时构建"模式——**当前 14 项依赖全部就绪**，但主树 package.json 未来新增依赖而镜像未同步时，重启构建会炸在 webpack 深处
  - 审计时的"容器内 dist 5-28"读数来自 backend 容器（未挂 frontend 的镜像残留视角），非服务视角——审计自身的教训：**容器视角要按服务容器核对，不能拿旁证容器下结论**
- **已落地修复**（2026-11-05）：两个前端启动脚本加 `check-node-deps.mjs` 依赖预检（require.resolve 语义，避免 exports 字段假阳性），缺包在启动第一阶段点名可执行动作，不再炸在 webpack 深处
- **残留观察项**：镜像内陈旧 frontend/dist（5-28）属迷惑性资产（本次审计即被误导过一次），建议下次镜像维护时清理或在 provenance 文档注明；miner-web 加新依赖时记得主树 `npm install` 后重启（预检会拦截）

### P1-6【部署】前端容器启动脚本每次重启从源码 npm run build
- **江西对应**：3.4（重启重建冲掉热更）
- **证据**：`docker/start-frontend.sh`、`docker/start-miner-web.sh` 均为 `npm run build` + `exec node static-server.mjs <dist>`
- **影响**：① docker cp 热更 dist 会被下次重启静默冲掉（江西多个修复因此"从未生效"）；② 重启期间分钟级不可用；③ 构建失败（依赖/内存）= 容器起不来；④ 生产环境跑构建逻辑本身非最佳实践
- **建议**：短期在部署文档里写明"热更必须改源码不能 cp dist"；长期改镜像内置成品 dist + 显式版本标签

---

## 2. P2 清单（特定条件触发 / 纵深缺失）

| # | 域 | 问题 | 证据 | 江西对应 |
|---|---|------|------|---------|
| P2-1 | API | Flask 全域无 Cache-Control；真实瓦片无正缓存；透明兜底瓦片被钉 24h（重切修复后旧浏览器仍透明一天）；410 停用响应无 no-store | `applications/__init__.py` 无 after_request；`miner/routes/tiles.js:37-41` | 2.1 |
| P2-2 | API | `fail_api` 默认 HTTP 200 + success=false（70 处调用不带 status）——只断言 200 的测试是假绿结构雷（抽查 80% 测试有实质断言，非江西式全量假绿） | `common/utils/http.py:11-19` | 2.7 |
| P2-3 | 后端 | `ensure_runtime_schema` 先查后 DDL 无并发容错，被 gunicorn 2 workers + inference-worker 三路并发调用，冷启动空库有竞态崩溃窗口（restart 自愈） | `applications/runtime_schema.py:25,30`；`__init__.py:45`、`inference/app.py:28` | 4.2 |
| P2-4 | API | 单发通道非栅格文件（jpg/png/gif 白名单内）无逐文件大小闸门——登录用户可单发 ≤15GB PNG，Werkzeug 全量内存解析（实测 573MB 峰值 1.78GiB） | `api/file.py:41-55`（1GB 闸门只查 is_tiff_file） | 2.6 |
| P2-5 | 后端 | kml_roi pipeline 主链测试全 mock（只断 status==succeeded 与调用参数，无像素级断言）；crop/tif_to_png 失败 6 处 `return False` 无日志静默跳 fid | `backend/test_kml_roi_pipeline.py:41-86`；`kml_roi/raster_ops.py:204-228` 无 LOGGER | 4.7 |
| P2-6 | 后端 | legacy `docker/entrypoint.sh:175-176` `wait -n` 一死陪葬模式 + miner-api/miner-web 进程不在监视集（现役 prod compose 分离服务不受影响，属离线/单容器形态风险） | `docker/entrypoint.sh:175` | 4.1 |
| P2-7 | 推理预处理 | ROI 小图斑无条件 `cv2.resize` 拉 512×512 不保纵横比（无 letterbox）；前端 `ImgShow.vue:133` `fit:"fill"` 二次变形展示 | `kml_roi/raster_ops.py:16,281-287`；`tiles.py:107-108` | 3.8 |
| P2-8 | 部署 | spatial-worker 运行态 health=none，与当前 compose 已定义的心跳判活（10-24 维护）漂移——容器是旧 compose 起的，判活实际未生效 | `docker inspect` health=none vs compose 定义 | 1.4 配套 |
| P2-9 | 部署 | 模型 9.5GB 在主树 `backend/model/`，靠 bind 遮蔽镜像层供 backend+worker 消费；不入镜像不入 git——离线包/迁移清单必须显式覆盖，否则换机即丢模型 | mounts：`./backend:/app/backend:ro` | 1.1 变体 |
| P2-10 | 部署 | `wait-for-mysql.py` 预算 30×2s=60s，MySQL 卷首次初始化可超 60s → 首启 exit 1 靠 restart 自愈 | `docker/wait-for-mysql.py` | 4.6 |
| P2-11 | BFF | miner-api 容器内 11-02 起存在 zombie `[curl] <defunct>` 未收尸（子进程管理漏洞信号） | `docker exec ps aux` | §6 工具链 |
| P2-12 | 越权 | `/api/file/upload/complete` 缺 `ensure_session_owner`（底图孪生端点有：`spatial_service.py:959`；chunk 写入有：`upload_sessions.py:199`）；利用需猜中 16-64 位 hex key，难度高 | `api/file.py:132-197` 全文无调用 | 5.8 |
| P2-13 | 安全 | `SECRET_KEY` 回退 `'dev key'` 仍在（`configs/config.py:24`）；生产已有双层 fail-fast（`__init__.py:37-38` + compose `:?`）——建议非 testing 一律拒绝回退 | config.py:24 | 5.4 |
| P2-14 | 安全 | 登录无限速/无失败锁定（后端+BFF 均无；缓解：werkzeug scrypt 哈希 + 弱口令部署闸） | `api/auth.py:9-20` | 5.5 |
| P2-15 | 安全 | miner 容器未设 `NODE_ENV=production` → Express/body-parser dev 错误页含堆栈 | `docker/start-miner-api.sh`、compose 均无 | 5.7 残留 |
| P2-16 | 部署 | 无系统化备份脚本；仅 6-06 手工 tar（5 卷，缺 `miner_uploads`）；恢复演练仅那一次 | `volume_backups_before_restore_20260606_193709/` | 1.7 |

---

## 3. P3 / 卫生

1. 主树 `miner/.env`（6 月 6 日，5 键）陈旧且非权威（容器内 env 由 `write-runtime-env.py` 从 compose 生成），易误导手工编辑（1.8）
2. `backend/model/__init__.py:Zone.Identifier`（Windows ADS 流文件）混入代码目录
3. spectral_indices 预览自实现全数组归一化，与共享 `normalize_array` 逐通道版本视觉级分叉（`interface/analysis.py:262-284`）
4. miner 9 个未定义 CSS 类（冗余语义钩子，无功能缺失）
5. `frontend/src/utils/loadMap.js` BMap 外部全局引用（有 typeof 守卫，安全）
6. recover_abandoned_jobs 的 `worker_id.like(f"{host}:%")` 在同主机多 worker 场景会误杀（当前 MAX_CONCURRENCY=1 + 容器 hostname 兜住）
7. 上传栅格校验失败"优雅降级保留"推迟错误暴露到推理期（`upload.py:100-110`）
8. `backend/app.py:31` `__main__` 块相对路径写 frontend/.env（仅直跑触发，gunicorn 不走）

---

## 4. 已防护 / 不命中（江西教训在云南的修复态资产）

以下项亲验通过，**不需要动作**，列出供后续审查对照：

| 清单项 | 状态 | 证据 |
|---|---|---|
| 1.4 worker 是否在跑 | ✅ 7 容器齐全，inference-worker healthy | docker ps 亲验 |
| 1.6 两项目数据混线 | ✅ 云南 4 卷扫描无江西文件 | 卷扫描亲验 |
| 1.8 env 单一权威 | ✅ config.yaml + compose env → write-runtime-env.py 生成链完整 | 脚本亲读 |
| 2.6 分片闸门主体 | ✅ 100GB/512MB 块/sha256/属主（chunk 侧）/失败不删原始文件/合并失败只删副本 | upload_sessions.py |
| 3.1 用了没 import | ✅ eslint no-undef 全量 98 文件，仅 BMap 有守卫 | 子代理实跑 |
| 3.2 模板引用未声明 | ✅ 45 SFC / 1097 引用 / 0 未声明 | 子代理自写脚本实跑 |
| 3.3 模板编译错误 | ✅ parse + compileTemplate 双 0 错误 | 子代理实跑 |
| 3.5 轮询触发全屏 loading | ✅ silent 旁路 + 计数对称（10-24 修复注释在案） | request.js |
| 3.6 v-if 按钮可达性 | ✅ 删除按钮无条件渲染 + record_source 三分支 | ImgShow.vue:15-18 |
| 3.7 列表临时卡 | ✅ 无临时卡模式；竞态门控 + 动作后回第一页 | 子代理核查 |
| 4.1 EPIPE 主链 | ✅ DB 轮询模式无 socket serve loop；`_isolate_failed_job` 注释自证修过江西同款 | worker.py:630-633 |
| 4.2 admin 播种竞态 | ✅ IntegrityError 回滚改更新（注释"江西 03247b9 同款修复"） | auth/service.py:27-38 |
| 4.3 后台线程 current_app | ✅ 无后台线程；worker 均在 app_context 内 | grep 零命中 |
| 4.4 推理取消语义 | ✅ queued 直收口 + 协作取消 + 发布前复查 + 状态机 + 取消 API 不静默 | inference/jobs.py、worker.py |
| 4.5 孤儿作业/陈旧锁 | ✅ 双 worker 均有重启回收（running→failed/queued）；flock 内核锁 | jobs.py:333-356 |
| 4.8 影像读取一致性 | ✅ RGB 提取/归一化同源 tiff_processor，推理瓦片显式复用；uint16 percentile 天然覆盖 | raster_ops.py:283-284 |
| 5.2 任意文件读取 | ✅ 全部路径端点白名单+受控根双消毒（90 条路由逐一核对） | 安全子代理路由清单 |
| 5.3 SSRF | ✅ 江西同款代码存在但前缀门拦死绝对形请求（两路独立验证一致：主控逻辑核验 + 子代理本地实验） | static-server.mjs:43,60 |
| 5.7 500 回显 | ✅ 全局通用文案 + 分型 error_responses + 5 项回归测试 | app.py:14-21 |
| 5.8 路由鉴权覆盖 | ✅ Flask 90 条 + miner BFF 全挂鉴权（60:60 装饰器核对） | 安全子代理清单 |
| §4.9 路径消毒 | ✅ resolve_storage_path/resolve_output_file/_validate_record_filename 全套 | spatial_storage.py 等 |
| requirements 漂移 | ✅ 容器内与主树 md5 一致 | 亲验 |
| 模型 git 管理 | ✅ 9.5GB 权重被 `backend/model/*` + .py 白名单正确忽略，仅 5 个代码文件入库 | git ls-files 亲验 |

---

## 5. 本轮未覆盖（建议下一轮）

1. **GUI 冒烟**（清单 §8.7）：登录→上传→任务→结果→删除全流程 + console/日志零异常——需真实浏览器验收
2. **全量测试门**（§8.8）：一次性容器权威跑法（bash + conda activate + PROJ 环境变量 + `--gpus all`），当前基线数字待重测
3. **P2-7 小图斑拉伸的实际误判验证**：需真实推理对比（letterbox 前/后），静态审查只能确认代码形态与江西同款
4. **离线包形态检查**（§1 的第三形态）：deploy_offline 流程 + 包内 dist/模型清单 vs 主树对账

---

## 6. 建议修复优先级（供负责人排序）

- **送测前必办**：P1-5（重建镜像）、P1-1（分片白名单）、P1-3（取消杀进程组，需 e2e）、P1-6（至少文档化热更纪律）
- **高价值低风险**：P1-4（request timeout/遮罩逃生口，几行改动）、P2-12（补一行 ensure_session_owner）、P2-15（NODE_ENV 一行）、P2-2（新增测试断言规范）
- **需要设计的**：P1-2（semantic_segmentation 异步化——改 API 契约，涉及前端两处调用）、P2-1（缓存策略分层）、P2-16（备份脚本）
- **观察项**：P2-7（先实测误判再决定 letterbox）、P2-3/P2-10（冷启动窗口，restart 已自愈）

---

## 7. 修复记录（2026-11-05，检查当日三批落地）

| 批次 | 项 | commit |
|---|---|---|
| 1 | P1-1 分片白名单（init+complete 双闸）+ P2-12 complete 属主校验 + P2-15 NODE_ENV + P1-4 前端超时/逃生口 | d51aad6 |
| 2 | P1-3 spatial 取消整组杀进程组（含真实三进程组 e2e 测试） | 887698f |
| 3 | P1-6 热更纪律文档化 + P2 纵深（static-server 显式 origin-form 断言/502 脱敏 + 启动依赖预检）+ 本报告 P1-5 定性修正 | 本批 |
| 4（2026-11-05 下午，生产化加固） | P2-4 单发普通图片 512MB 闸门 / P2-13 SECRET_KEY 非 testing 拒绝回退 / P2-14 登录失败限速（15min×5 锁定）/ P2-5 raster_ops 六处静默失败补日志 / P2-10 wait-for-mysql 预算 60s→180s / P2-1 Cache-Control 分层（API no-store、瓦片 24h、透明兜底 60s、鉴权 410/429 no-store）/ P3-1 miner/.env 权威性注释 / P3-2 Zone.Identifier 清理。**审计未列出的新增**：切片任务 deadline（SPATIAL_JOB_TIMEOUT_SECONDS 默认 6h，卡死任务不再永 running）/ SQLALCHEMY pool_pre_ping / 全容器日志轮转 20m×3 / 容器 mem_limit / /api/system/version 版本自描述（YUNNAN_GIT_SHA 注入）/ X-Request-Id 生成回显+5xx 日志携带 / 前端全局错误留痕（errorSink，两 app）/ 备份脚本 docker/backup_yunnan.sh + docs/backup-restore.md / MySQL 慢查询日志 / GeoView 直连瓦片超 maxZoom 透明兜底（10-28 遗留②收口）。测试：test_production_hardening.py 10 例 | 见 git log |

**验证**：后端全量 2305 passed + 1337 subtests（新增 5 测试）；frontend 容器构建通过；compose config OK；static-server 实测绝对形 400/正常 200；依赖预检三环境实跑（本机 frontend 28 项/miner 10 项、容器 miner-web 14 项全就绪）。

**未修（待负责人决策）**：P1-2（semantic_segmentation 异步化——涉及 API 契约与前端两处调用方，需设计）；P2-1（缓存策略分层）、P2-2（fail_api status 规范）、P2-16（备份脚本）；P2-7（小图斑 letterbox——先实测误判再动手）。
