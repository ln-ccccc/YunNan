# 验收清单 —— 江西问题迁移检查与修复（2026-11-04 ~ 11-05）

> **工作线**：11-04 对照江西五轮审查教训对云南做全量地雷图检查（主控谱系对账 + 3 路并行子代理审查，产出 docs/yunnan-migration-audit-20261105.md，P1×6/P2×16/P3×8）→ 11-05 修复三批 + 全局自审 + 部署生效 + GUI/API 冒烟 + push。
> **代码范围**：`main` 分支 `dcdf9be..8565970` 共 5 个 commit（均已 push），合计 17 文件 +771/-15。
> **本文档目的**：验收人按 §3 验收点逐项复验；每项附改动文件、行为变化与复验方法。

---

## 1. Commit 清单

| commit | 时间 | 内容 | 改动 |
|---|---|---|---|
| d51aad6 | 11-05 09:39 | 批次1：分片上传白名单 + complete 属主校验 + NODE_ENV + 前端超时/遮罩逃生口 | 6 文件 +180/-8 |
| 887698f | 11-05 09:41 | 批次2：spatial 切片取消整组杀进程组（含真实进程组 e2e 测试） | 2 文件 +89/-3 |
| 63a303b | 11-05 09:46 | 批次3：热更纪律入册 + static-server 纵深加固 + 启动依赖预检 + 两份审查文档入库 | 7 文件 +487/-4 |
| bafc63d | 11-05 10:03 | 补测：complete 对不存在会话仍 400（锁校验顺序变化后的异常语义） | 1 文件 +8 |
| 8565970 | 11-05 10:19 | P1-5 真身：frontend 容器显式挂主树 package.json+node_modules（生效执行时爆雷的修复） | 1 文件 +7 |

## 2. 改动明细（改了什么 / 为什么）

### A. 安全（对应审计 P1-1 / P2-12 / P2-15）

| # | 文件 | 改动 | 行为变化 |
|---|---|---|---|
| A1 | `backend/applications/api/file.py` | 分片通道扩展名白名单（`CHUNKED_UPLOAD_EXTENSIONS`，与单发 IMAGES_WITH_TIFF 同源、剔除 ENVI）；init + complete 双闸 | 之前任意后缀（.svg/.html）可经分片落盘并被 `/_uploads` 按扩展名内联提供（存储型 XSS）；现在 init 阶段即 400 |
| A2 | 同上 | complete 补 `ensure_session_owner`（与 init/chunk 对齐） | 登录用户不能再抢先完成他人上传会话 |
| A3 | `docker-compose.prod.yml` | miner-api 加 `NODE_ENV: "production"` | Express 不再以 dev 模式回显错误堆栈 |
| A4 | `backend/test_file_upload_chunks.py` | 新增 6 个测试：init 拒 svg / ENVI 单发引导 / complete 拒模块层 svg 会话 / 双登录属主隔离 / 未知会话 400 | 锁死上述行为 |

### B. 前端死锁组合拆除（P1-4，江西 2.2 同款）

| # | 文件 | 改动 | 行为变化 |
|---|---|---|---|
| B1 | `frontend/src/api/request.js` | axios 实例默认 `timeout: 60000`；超时与断链分开文案 | 后端饱和时非 silent 请求 60s 后报"请求超时，任务可能仍在后端处理"，不再无限 pending |
| B2 | `frontend/src/api/upload.js` | 长端点豁免：analysis 同步推理/大文件单发/分片合并 `timeout:0`（后端 gunicorn 1800s 兜底）；image_pre 300s | 长任务不被 60s 默认误杀 |
| B3 | `frontend/src/utils/loading.js` | 全屏遮罩 15s 逃生口（自动解除+提示，请求不中止）；endLoading 判空 | 死锁组合（无超时+lock 遮罩）拆除；逃生后界面可继续操作 |

### C. 后端 worker（P1-3，江西 4.4 同款）

| # | 文件 | 改动 | 行为变化 |
|---|---|---|---|
| C1 | `backend/applications/project_hub/spatial_worker.py` | `Popen` 加 `start_new_session`；`_terminate` 改 killpg 整组 SIGTERM→10s→SIGKILL（组已死/平台无 killpg 回退单点） | 取消切片后 gdal2tiles 的 multiprocessing 池子进程不再孤儿化 |
| C2 | `backend/test_project_spatial.py` | 新增真实三进程组 e2e 测试（父+两池子进程整组回收断言；非 mock） | 锁死 killpg 行为 |

### D. 部署/纵深（P1-5 / P1-6 / P2-8）

| # | 文件 | 改动 | 行为变化 |
|---|---|---|---|
| D1 | `docker-compose.prod.yml`（8565970） | frontend 挂载 `./frontend/package.json:ro` + `./frontend/node_modules`（rw） | 消除"src 是新代码、node_modules 是镜像旧版"的依赖漂移（生效执行时已实际爆雷：4 包 MODULE_NOT_FOUND crash loop） |
| D2 | `docker/check-node-deps.mjs`（新增）+ 两个 start-*.sh | 启动时依赖预检（require.resolve 语义） | 缺包在启动第一阶段点名可执行动作，不再炸在 webpack 深处 |
| D3 | `docker/static-server.mjs` | 显式 origin-form 断言（非 `/` 开头请求 400）；502 不回显 error.message | SSRF 防护从前缀巧合升级为显式拒绝；不再泄露内网目标主机/端口 |
| D4 | `docs/docker_restart_guide.md` | 新增 §8 前端热更纪律 | 禁止 docker cp dist 式热更（重启必被冲掉）；重启=重建的预期写明 |
| D5 | `docs/yunnan-migration-checklist-20261104.md`、`docs/yunnan-migration-audit-20261105.md` | 江西教训对照清单 + 云南全量审查报告（含修复记录）入库 | 验收参考的正文档 |

## 3. 验收点与复验方法

环境：运行栈已按新配置 recreate/restart（冒烟时验证过），四端口 3000/4000/5008/8000 正常。

**V1 分片白名单（A1）**——登录后：
```bash
curl -s -b <cookie> -X POST http://localhost:5008/api/file/upload/init \
  -H "Content-Type: application/json" \
  -d '{"upload_key":"<40位hex>","filename":"evil.svg","total_size":1024,"chunk_size":1048576}'
# 预期：400 + "分片续传仅支持影像/图片格式（…），当前文件后缀 .svg 不受支持"
```

**V2 complete 属主校验（A2）**——登录 A 建会话后，用另一个登录用户（或直接用未知会话）：
```bash
curl -s -b <cookie> -X POST http://localhost:5008/api/file/upload/complete/<未知40位hex> -d '{}' -H "Content-Type: application/json"
# 预期：400 + "上传会话不存在或已过期"（不是 500）
# 双登录属主隔离的自动化验证见 test_file_upload_chunks.py::test_complete_rejects_hijack_by_other_logged_in_session
```

**V3 NODE_ENV（A3）**：`docker exec yunnan-miner-api printenv NODE_ENV` → `production`

**V4 前端超时/逃生口（B1-B3）**——代码审查为主；GUI 可验：上传一个大图触发长推理，15s 后遮罩自动解除并出现"请求仍在处理中"提示，页面可继续操作。

**V5 取消整组杀（C1）**——自动化已覆盖：一次性容器跑 `test_project_spatial.py::TestSpatialTerminateKillsWholeProcessGroup`（真实三进程组）。

**V6 依赖预检（D2）**：`docker logs yunnan-frontend | grep check-node-deps` → `[check-node-deps] /app/frontend: 28 项依赖全部就绪`（注意：数字应为主树 package.json 的 28 项=挂载生效证明；若显示 24=镜像旧版=挂载未生效）。

**V7 SSRF 断言（D3）**：`curl -s -o /dev/null -w "%{http_code}" "http://localhost:3000/api/公共前缀" -X GET --request-target "http://192.0.2.1/x"` 或等价的绝对形请求（Python http.client 可发）→ 400；正常 `/` 请求 → 200。

**V8 全量回归**（标准跑法，一次性容器）：
```bash
MSYS_NO_PATHCONV=1 docker run --rm --entrypoint sh \
  -e PROJ_LIB=/opt/conda/envs/MMSeg310/share/proj \
  -e GDAL_DATA=/opt/conda/envs/MMSeg310/share/gdal \
  -e PROJ_DATA=/opt/conda/envs/MMSeg310/share/proj \
  -v "D:\项目\YunNan:/app" yunnan-runtime:current \
  -c "cd /app/backend && pip install -q pytest 2>/dev/null; python -m pytest test_*.py -q --tb=line -p no:cacheprovider"
# 本轮修复后的实测基线：2307 passed, 53 skipped, 1337 subtests passed（约 9 分钟）
```

**V9 已做过的冒烟记录**（11-05 实测，验收人可抽复任意项）：GeoView/miner 双前端登录渲染正常（截图存证于会话）；分片上传 e2e 全链路（init 幂等→chunk sha→complete 双闸→产物下载 200）；真实推理 job 提交→取消 5s 收口 cancelled；五容器日志无持续异常。

## 4. 诚实边界（未做/已知限制）

1. **P1-2 未修**（semantic_segmentation 等同步长端点异步化）——方案已定（完整异步化），属下一轮，涉及 API 契约与前端两处调用方。
2. **P2 批次未做**：P2-1 缓存策略 / P2-2 fail_api status 规范 / P2-16 备份脚本 / P2-7 小图斑 letterbox（待实测误判再定）。
3. V4 的 15s 逃生口在"同批请求滞留期间新请求无遮罩反馈"上有已知取舍（宽松优于死锁，与江西定案一致）。
4. inference-worker 在宿主内存 <4GB 时对 CPU 回退任务预检拒绝（设计行为，非本轮引入）；验收机内存充足即不触发。
5. miner-web 容器仍用镜像 node_modules（当前 14 项自洽就绪）；其主树 node_modules 缺 4 项未补——补齐前不给 miner-web 挂主树 node_modules（compose 注释已说明）。
