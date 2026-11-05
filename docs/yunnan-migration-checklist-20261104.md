# 云南项目检查清单 —— 江西项目问题迁移（2026-11-04）

> **来源**：江西项目与云南同源（miner BFF/Express + Flask 后端 + 解译前端 + Docker 部署）。本清单浓缩江西 2026-09～2026-11 五轮全面审查（code-review-20260914/16/19/21）、送外测前第五轮测试（pre-delivery-test-20261102，6 个 P0）及日常运维事故的教训。
> **用法**：每条 = 江西发生了什么（带证据）→ **云南检查动作**。可按章节拆给并行子代理当地雷图（江西审查工作法：并行分治 + 地雷图注入 + 主控亲验）。
> **证据索引**：江西仓库 `JiangXi-Platform/docs/`（各轮 code-review-*.md、pre-delivery-test-report-20261102.md）+ JiangXi-Platform/AGENTS.md §5/§7。本文件目前未提交，是否入库由负责人定。

---

## 0. 优先级总览

| # | 风险域 | 江西教训烈度 | 说明 |
|---|--------|------------|------|
| 1 | 部署/镜像/数据谱系 | **送测 6 个 P0 中 5 个在这类** | 镜像分叉、bind 遮蔽、worker 未启动、目录错位、数据混线 |
| 2 | API 缓存/超时/异步化 | P0×1 + 一整轮整改 | 410 缓存钉死、无超时死锁、同步长端点占满并发 |
| 3 | 前端运行时 | 白屏 P0×2 | 构建/单测/契约门全部测不出 |
| 4 | 推理 worker 生命周期 | P0（容器每推理一次就死） | EPIPE 链 + 孤儿作业 + 取消语义 |
| 5 | 安全四件套+ | 审查 P0/P1 级 | XSS/任意文件读/SSRF/SECRET_KEY |
| 6 | Windows/Docker 工具链 | 反复踩 | CRLF、docker cp、可写层丢失 |
| 7 | 测试方法论 | —— | 江西最终收敛的"怎么查才查得出" |

---

## 1. 部署 / 镜像 / 数据谱系（最高优先）

江西送外测前测试结论：**三种候选形态（生产容器/QA 容器/离线包）没有一个能直接送**，5/6 个 P0 都是这一类。

- [ ] **1.1 bind mount 遮蔽资源目录**
  江西：生产容器 model 目录被 YunNan bind mount 遮蔽，导致江西模型"消失"（送测 P0-3）。
  **查**：对每个现役容器 `docker inspect <c> --format '{{json .Mounts}}'`，逐条确认挂载点没有盖住镜像内的模型/资产/瓦片目录；容器内 `ls` 实际生效目录，与镜像层内容对比。

- [ ] **1.2 可写层资产 vs 镜像固化（docker run 丢、restart 不丢）**
  江西：瓦片符号链接建在容器可写层，`docker run` 新容器底图全黑（server.js:105 无回退），`docker restart` 却不丢——问题被长期掩盖（送测 P0-1）。部署谱系瓦片在 `dist/tiles`、代码取 `public/tiles`，靠符号链接弥缝，重建即丢。
  **查**：`docker diff <容器>` 列出可写层改动，逐项问"重建容器后它还在吗"；符号链接/补丁文件必须固化进镜像或挂卷；server 端资源路径要有存在性检查+回退。

- [ ] **1.3 镜像谱系分叉（未入库的"只在容器里"改动）**
  江西：部署镜像 yunnan-workflow:39e777e（10-24）带未入库改动——停用了全局地图接口（retiredGlobalMapEndpoints→410）+ 瓦片目录迁移，git 分支里根本没有这些改动，图斑"消失无法复原"的双重根因之一。换基座时 1152 行的 analysis.py 被换成 517 行版，kml_roi 全链断（送测 P0-5）。
  **查**：现役镜像 tag 能否对应到 git commit（`docker history`/label）？容器内关键文件与 git HEAD diff 一下（`docker exec` + md5 对比）；凡是"容器里有、仓库里没有"的改动要么入库要么明确废弃。

- [ ] **1.4 推理 worker 是否真的在跑**
  江西：生产容器 start.sh 里 inference_worker 出现 0 次——推理服务压根没启动（送测 P0-2）；QA 镜像反而有。
  **查**：`docker exec <c> ps aux | grep -i worker/inference`；看启动脚本（注意**真实入口可能不是 docker/entrypoint.sh**，江西部署镜像实际是 /app/release/start.sh）；healthcheck 是否覆盖 worker 存活。

- [ ] **1.5 上传/输出目录配置错位**
  江西：path_global.py 上传目录与实际挂载不一致，上传成功但下游找不到（送测 P0-4，QA 镜像已修、生产未修）。
  **查**：上传一个测试文件，从"落盘路径"反查所有消费方（切片/推理/预览/导出）是否解析到同一物理位置；配置项与挂载点对账。

- [ ] **1.6 两项目数据混线**
  江西：云南模型/大理影像/yunnan 项目目录混入江西生产+QA 卷（送测 P0-6）；反向同理。
  **查**：云南的卷/镜像/project_storage 里有没有江西的模型、KML、影像、env 段；镜像共用层（yunnan-workflow 系）是否把对方资产烘焙进去了。命名空间（JX*/YN* 前缀）隔离是否成立。

- [ ] **1.7 热更备份完整性**
  江西：生产热更前打的备份 tar 缺 22 个 services/routes 文件——备份脚本没覆盖全。
  **查**：备份/快照脚本（volume_backups_*、deploy_offline）的文件清单 vs 实际运行目录树 diff；恢复演练至少做过一次。

- [ ] **1.8 env 文件单一权威**
  江西：env 文件多版本并存，旧谱系腐化（曾出现 MINER_LOCAL_TILE_URL 被污染，靠启动时 regex 自愈兜底）；Temp 目录的 env 已判死仍差点被复用。
  **查**：`.env`/`image_bundle.env`/各 compose 的变量来源对账；有没有已废弃 env 仍被脚本引用；敏感变量（SECRET_KEY/密码）是否散落多份。

---

## 2. API 层：缓存 / 超时 / 异步化 / 回退 / 上传

江西 2026-11-02 一天内做了三轮整改（2f9a4f3→de4da9b→25faf78+f8ec333），这一章基本是现成处方。

- [ ] **2.1 API 响应缓存策略必须显式**
  江西：旧 410 响应无 Cache-Control，被浏览器缓存钉死→地图图斑"消失且无法复原"（用户级故障）。修复=/api 统一 no-store；后加固为：Flask after_request 给 /api/+/static/ no-cache（含 /_uploads/），miner /api no-store→no-cache（大 geojson 未变走 304），成果目录 no-cache，/tiles 反向加 max-age 7 天正缓存。
  **查**：curl -I 逐类端点看 Cache-Control；"会被更新的资源"绝不能依赖默认缓存策略；停用接口（410/404）响应头尤其要有 no-store。

- [ ] **2.2 全链路超时**
  江西：①会话检查请求无 timeout→被占住即静默死锁（用户报"切换无反应"）；②POST 全无超时 + loading 模块级计数→全屏遮罩组合死锁；③miner 启动链 4 处子进程无超时可永久锁死（20260914 P1）。修复=前端 session/login/logout 10s、GET 30s、写请求补 22 处 + loading 遮罩 15s 逃生口（滞留自动解除+通知）。
  **查**：grep 前端 request 封装与 fetch/execFile 调用，列出无超时清单；长计算 POST 不能一刀切超时的，必须有遮罩逃生口或改异步。

- [ ] **2.3 长任务异步化（submit + 轮询 + 取消）**
  江西：同步长端点（推理/光谱）+ gunicorn timeout=0 → 2×4 并发被占满即整站无响应；D1 定案全部异步化（POST submit / GET status / POST cancel），前端轮询带 **404 自动回退同步**——新端点未部署的旧后端上功能不断。
  **查**：云南 AGENTS.md 已有 Job.status 四层状态模型，核对推理/切片/大文件处理是否全部走后台 Job；同步遗留端点还有几个；取消能力是否真实（见 4.4）。

- [ ] **2.4 前端消费新端点必须有回退**
  江西：kml_roi 前端调 kml_roi_submit 无 404 回退（只有光谱有）→ 换了无该端点的后端谱系即全链断（送测 P0-5 的表现层）。
  **查**：所有"新端点"消费方 grep 一遍，无回退模式的补齐。

- [ ] **2.5 错误包装必须带 kind 标记透传**
  江西：request.js 包装把 response 丢了→前端断链判定恒真（系统性模式，20260921 发现）。
  **查**：前端 HTTP 封装层错误对象结构；消费方按 kind 分流而不是猜。

- [ ] **2.6 上传闸门口径一致 + 失败保留原始文件**
  江西：三层闸门口径互相打架——upload 8GB / preprocess 500MB / Flask 无 MAX_CONTENT_LENGTH（9GB png 实测可落盘）；曾出现切片预览失败**把用户原始 tif 删掉**的事故（keepRawTiff=true 快速路径修复）。
  **查**：云南 2026-09-22 已落地分片续传，核对：全局 MAX_CONTENT_LENGTH 存在且覆盖所有扩展名；各处大小上限一致；任何处理失败路径都不会删除原始上传文件；魔数校验（不只是扩展名）。

- [ ] **2.7 测试断言别只看 HTTP 状态码**
  江西：fail_api 语义是 HTTP 200 + success=false——只断言 200 的测试全假绿。
  **查**：云南测试里 `assert status==200` 后有没有再断言业务字段。

---

## 3. 前端运行时（构建/单测/契约门全部测不出的 P0）

江西两次整站级 P0 都是"静态门全绿、GUI 一跑就炸"。云南 AGENTS.md §8 自己也写了"UI 测试/构建不等于真实浏览器验收"——对照以下模式逐条排。

- [ ] **3.1 「用了但没 import」→ 路由挂起整站白屏**
  江西：276cc9f 在 Segmentation.vue 引用 flashHistoryDeleteRun 但 import 块漏加；dev 下软警告+undefined 绑定→路由永久挂起；prod 压缩后 ReferenceError 炸分块。270 单测+44 契约+build 全绿测不出。修复 500769b。
  **查**：对 miner/frontend 跑一遍静态分析（eslint no-undef 严格档 / vue-tsc / 手动 grep methods 引用 vs import）；重点看近期大改动的 SFC。

- [ ] **3.2 模板引用未声明的 data/computed → 永不渲染**
  江西：TerraIntro 模板 v-if="selfVisible" 但 data() 未声明→canvas 永不渲染→initScene 读 $refs undefined→永久卡死（8f4be4e）。与 3.1 同类。
  **查**：模板中引用的每个响应式标识符是否都在 data/computed/props 声明（可用编译器或脚本扫）。

- [ ] **3.3 .vue 模板改动必须用解析器验证**
  江西：脚本盲改把新按钮嵌进原按钮 @click 字符串，ImgShow.vue 模板 4 处编译错误→vue-loader 输出**空模块**→两页结果预览全部空白（用户报"结果不可显示"的总根因）。修复后用 vue/compiler-sfc parse + compileTemplate 验证 0 错误。
  **查**：把 `@vue/compiler-sfc parse+compileTemplate 全量 0 错误` 加进云南前端验证门（脚本一次性投入，永久防这类雷）。

- [ ] **3.4 dev server 重编译窗口 / HMR 静默失败**
  江西：①docker cp 换 .vue 后 webpack HMR 可能服务陈旧或空模块，清 node_modules/.cache + 重启才恢复；②重编译窗口内路由懒加载分块静默失败（hash 变了组件不换、无报错）；③容器 start-frontend.sh 每次重启会从源码 npm run build，**docker cp dist 的热更被冲掉**（多个修复因此从未生效）。
  **查**：云南容器的启动脚本是否也有"重启即重建"；热更工作流的文件清单是否只覆盖 src；GUI 验收一律整页 reload（hash 跳转不重载 JS）。

- [ ] **3.5 轮询请求触发全屏 loading**
  江西：request 拦截器对状态轮询也弹全屏遮罩→推理期间遮罩每 5s 闪烁（用户报障）。修复=封装加 noLoading 旁路，轮询传 noLoading:true（89884de）。
  **查**：云南 HTTP 封装的拦截器对轮询/心跳类请求是否豁免全局 loading。

- [ ] **3.6 v-if 分流把整类卡片的按钮藏掉**
  江西：ImgShow 删除按钮 `v-if="item.record_id"` 对某类历史条目恒假→按钮永不渲染（P0，9a8944a 回归引入）。另：CSS 类引用了从未定义的样式类（primary-btn）→按钮浏览器默认白底刺眼。
  **查**：历史/列表类页面的条件渲染，逐类条目走查按钮可达性；grep 模板里的 class 名 vs 样式定义。

- [ ] **3.7 列表刷新覆盖临时卡 / 排序挤页**
  江西：①getMore 历史刷新整体覆盖 imgArr→合并卡"闪一下就没了"；②历史排序"未联动恒在前"攒满一页后把新结果挤到第二页（用户以为丢数据）；③提交响应造的临时卡与真实历史不同构，Run 实体化重构（S1）才根治——历史=后端单一来源，前端不造卡。
  **查**：云南列表页有没有"提交响应直接上屏"的临时卡模式；分页排序规则对新增记录的影响。

- [ ] **3.8 小图斑强拉伸引发模型误判**
  江西：tif_to_png 把裁片无条件拉到 512×512，小图斑（原生 84×123px）拉 4-6 倍→周边林地整片误判水体。逐图斑原生分辨率切片改造（2cef9cb）后消失，留回退开关。
  **查**：云南推理预处理里有没有类似"统一 resize 到模型尺寸"的路径，对极小/极大输入的行为。

- [ ] **3.9 删除语义要保护下游统计源**
  江西：删除结果图+src 但**刻意保留 mask/CSV**（miner 统计依赖）；U 目录删除带 `$confirm` + 白名单正则；一键清空语义（全量）与单删语义（保活文件）分开定义。
  **查**：云南删除/清空接口的连带删除范围是否显式定义、有没有测试锁语义。

- [ ] **3.10 懒加载图片验证要 scrollIntoView**
  江西：element-plus el-image lazy 只听滚动容器（.el-main）scroll 事件；无头验证需先 scrollIntoView 再等 img.complete（playbook T38）。终态判定禁用全文正则——面板图例印着"计算失败"等状态词，首轮即假阳性（T39）。
  **查**：云南 GUI 自动化脚本如出现"图片空白"，先按此排除环境伪象。

---

## 4. 推理 worker 与后端管线

- [ ] **4.1 EPIPE 链：一次客户端断连杀死整个容器（P0）**
  江西：healthcheck 短超时请求在长推理期间被抛弃→stale 连接留在 worker 监听队列→worker 响应后 accept 到它、读到 EOF、向已关闭 peer 发送→EPIPE 逃出 serve loop（send 在 try 外）→worker 退出→entrypoint `wait -n` 把全部服务陪葬→容器 exit(1)。**每次推理成功后容器必死**。修复（PR#1 已合并）：serve loop per-connection try/except（单个客户端死亡永不杀服务）+ entrypoint 自动重启 supervisor + 回归测试。
  **查**：云南 worker 的连接处理循环是否有 per-connection 隔离；entrypoint 是否 `wait -n`（会一个进程死全容器陪葬）；healthcheck 与 worker 的超时配置关系。

- [ ] **4.2 多 worker 播种竞态**
  江西：gunicorn 2 worker 下 admin 播种撞 UNIQUE 约束（首次部署偶发 500）。修复=幂等+回滚。
  **查**：云南启动时初始化逻辑（建表/播种/迁移）是否幂等；多实例并发下安全。

- [ ] **4.3 后台线程不能用 current_app**
  江西：D1 异步化踩坑——Flask 后台线程里 current_app 上下文已丢。
  **查**：云南 Job 执行器如在线程/子进程里访问 app/config/db，确认上下文传递方式。

- [ ] **4.4 取消与断线语义（含测试盲区）**
  江西：定案=断线**不**自动取消（大任务几十分钟，WiFi 抖动杀掉代价太大）；轮询连续失败 24 次（约 2 分钟）才按断链上报；用户主动取消 killpg 整组杀子进程。**取消传参 TypeError 被"取消标记兜底"静默吞掉，单测全 mock 没暴露，靠生产 e2e 抓获**——线程/子进程类改动必须在真实容器 e2e。
  **查**：云南 Job 取消链路真实跑一遍（不是 mock）：kill 的是进程组吗、作业状态会收口 cancelled 吗、串行锁/队列位会释放吗。

- [ ] **4.5 孤儿作业与陈旧锁**
  江西：worker 崩溃后推理子进程孤儿化，作业文件持 flock → 后续提交被 6h 封锁。修复=submit 前 reap + pid 判死 + 6h 陈旧守卫 + 完结档案 7 天 GC。
  **查**：云南 Job 存储里的陈旧 running 记录谁来判死；服务重启后 in-flight 作业的收口路径。

- [ ] **4.6 worker 就绪预算（离线/冷启动）**
  江西：镜像冷启动 import+模型加载实测 151s，entrypoint 硬编码 120 次轮询必超→首启 exit 1 靠 restart 自愈。修复=就绪预算参数化（默认 600 次）。教训：**首启超时预算按冷缓存实测值设定**。
  **查**：云南启动脚本的就绪等待次数 vs 目标机冷启动实测；超时后的行为（失败退出 vs 重试）。

- [ ] **4.7 跨函数 tag/键名契约**
  江西：合并预览 tag 契约错位（一端年份字符串、一端 "single"）→贴片全部静默 continue→结果图==底图（字节相同）。用户看到"左右两张图完全一样"。教训：**集成测试断言"画了像素/结果≠输入"，而非"文件存在"**；跨函数 tag 契约在两端测试里锁字面值。
  **查**：云南管线类测试有没有"输出与输入不同"的实质断言；静默 continue/pass 的贴片/映射循环。

- [ ] **4.8 多波段/uint16 影像处理一致性**
  江西：整图路径直接取前 3 波段无归一化，与联动路径（read_tiff_as_rgb）不一致→uint16/4 波段影像**结果错而不报错**（P1-1）。无地理配准 tif 曾直接 RuntimeError，后退化像素坐标。
  **查**：云南各条影像读取路径的波段提取/归一化是否同源一个函数；异常地理信息输入的退化行为。

- [ ] **4.9 路径消毒与白名单**
  江西：删除接口用 `_COVERED_RE 白名单 + relative_to` 双消毒；run_id 路由用专门白名单（复用 resolve_output_directory 的正则会漏）；URL 里 `..` 被 Werkzeug 折叠成 404（到不了视图，但不能依赖这层）。
  **查**：云南所有接受文件名/路径参数的路由（成果/导出/历史）逐个过白名单；AGENTS.md §6 已禁止浏览器提交任意 output_dir——grep 验证执行面。

- [ ] **4.10 性能基线（参考值）**
  江西：GPU tile batching（同形 tile 合批，JIANGXI_MMSEG_BATCH_SIZE=4）warm 推理 -45%，VRAM 峰值 3.25GB（8GB 卡安全）；冷启动首轮仅 6%（CUDA warm-up 干扰，**warm 数字才是真相**）；CPU 模式每子进程重载 2.4GB 模型=72s/图斑（resident worker 是方向）。
  **查**：云南推理如有性能诉求，先测 warm 基线再动手。

---

## 5. 安全（江西审查抓过的高危，同源代码命中概率高）

- [ ] **5.1 SVG 存储型 XSS**：IMAGES 允许集曾含 svg（script 可达）。查上传扩展名允许集。
- [ ] **5.2 任意文件读取**：光谱接口 kml_path/raw_tiff_path 直接收路径参数（P1）。查一切收路径的 GET。
- [ ] **5.3 SSRF**：static-server 代理分支 `new URL(request.url, base)` 可被绝对 URL 覆盖 base，method/body 全转发（PoC 200）。修法=origin 校验。**江西当时只修了静态分支漏了代理分支**——查修过的分支有没有孪生兄弟。内网部署是缓解不是豁免。
- [ ] **5.4 SECRET_KEY 静默回退 'dev key'**：生产缺配置不报错而是用弱默认。查所有密钥类配置 fail-fast。
- [ ] **5.5 登录限速 / 弱口令 fail-fast**：S4/S5 修复项。
- [ ] **5.6 上传魔数校验**：只查扩展名挡不住伪装文件。
- [ ] **5.7 500 回显内部错误原文**：kml-roi 500 曾把原始 traceback 回给客户端。
- [ ] **5.8 越权**：成果类路由 login_required 覆盖率（江西曾给新路由补 login_required）。

---

## 6. Windows / Docker 工具链（反复踩的坑）

- [ ] **6.1 CRLF 打死容器 .sh**：Windows `Path.write_text` 默认 CRLF→容器 `set: pipefail\r` 报错起不来。写容器内脚本必须 write_bytes/LF；`.gitattributes` 全覆盖 sh/py/js/vue/css/json/md；`git checkout -- file` 会跳过 stat 未变文件，要 rm 再 checkout 强制。**离线包打补丁用 git blob 里的 LF 版本。**
- [ ] **6.2 docker cp 目录必须 `src/.`**：否则目标目录多一层嵌套（江西热更踩过）。
- [ ] **6.3 restart/rm 与可写层**：`docker restart` 保留可写层，`rm+run` 不保留→重放清单要现成；**换容器前必须重做数据快照**（江西丢过用户 14:18 上传的源文件——快照是 13:40 的）。
- [ ] **6.4 docker commit 两踩坑**：①必须 `--change 'ENTRYPOINT [...]'` 显式重打并 inspect 核验（曾把临时容器的 sleep 入口 commit 进去×2）；②export/import 后 ENV 需人工重打且勿丢 PATH（丢 PATH→Node.js missing）。
- [ ] **6.5 Git Bash**：`MSYS_NO_PATHCONV=1`（POSIX 路径会被转码成 D:/Git/...；本地 /tmp 解析到 D:\tmp）；`| head` 会 SIGPIPE 杀长驻前台进程（dev server）；批量改码别用 heredoc 内嵌 python（引号炸），用 Write 写补丁脚本文件；正则改签名撞尾逗号会产生 `,,`。
- [ ] **6.6 端口**：端口契约如固定（江西 4173/4174/5178 烘焙进 dist，改端口需重 build）；Docker Desktop 端口冲突启动失败后 HostConfig 损坏（stop/start 无效）→ commit+rm+run 重建。
- [ ] **6.7 Docker Desktop 崩溃恢复**：全命令命名管道 500 → taskkill Docker Desktop.exe + `wsl --terminate docker-desktop` → 引擎自愈 → 容器按 restart 策略恢复、手动 docker start 补齐。**引擎重启会杀容器内后台进程**（长任务需检查断点续跑）。
- [ ] **6.8 裸 entrypoint 容器 PATH 无 conda**：`--entrypoint bash` 后必须用 `/opt/conda/envs/<env>/bin/python` 全路径；交互 `which python` 的结果不代表服务运行环境（江西活栈=conda，/opt/venv 只是测试用还破损）。
- [ ] **6.9 离线包四则**：apt 依赖闭包必须在**目标 rootfs 内**解析（标准镜像上算的闭包到目标机缺链）；任何 docker run 不得引包外镜像（种子灌入别用 alpine，用包内镜像自身 `--entrypoint bash`）；首启超时按冷缓存实测；Windows 补丁 KB 可能已被官方下架（EP 需内部渠道）。
- [ ] **6.10 npm 离线构建**：`npm ci --offline` 在 package-lock 新增依赖而无离线缓存时失败——新 devDeps 装到包外目录，用 createRequire 回退链解析。

---

## 7. 测试与验收方法论（怎么查才查得出）

江西最终收敛的工作法，做云南检查时直接沿用：

1. **并行子代理分治**：backend/frontend/miner/infra 各一，独立上下文互不知结论（防锚定）。
2. **地雷图注入**：任务书必含项目契约 + 本清单的已知模式，指令写成"已知模式 X，找同类未修实例"——江西第三处 refs 断线、static-server 代理分支 SSRF 都是这么抓的。
3. **契约驱动跨层对照**：前后端字段名/payload/键名 grep 对照（江西 kml_update 死键：BFF 读的键在生产者输出里根本不存在，恒 null）。
4. **修复声明 vs 实现逐条对照**：注释/提交声称修了什么，逐条验证——江西第二轮 4 个 P1 里 3 个是"声明与实现的缝隙"（收口漏改/死键/守卫失效）。
5. **主控亲验**：子代理报的每条 P0/P1 亲自确认才采信（压制幻觉）；两个独立代理交叉发现同一问题=免验采信信号。
6. **测试车辆陷阱**：①镜像内两套 Python（venv 可能破损，真环境是 entrypoint 激活的 conda）；②镜像 ENV 烘焙的 STANDALONE_MODE 类开关要显式覆盖；③GPU 耦合测试要 `--gpus all`；④一次性测试车辆不激活 conda 会有 'python' PATH 假红——**权威跑法：bash + conda activate 后再跑**。
7. **GUI 实测约定（江西用户铁律）**：改码后必须部署到运行容器→GUI 实际跑受影响流程→查前端 console + 后端 docker logs。单测绿/build 过只是及格线。IAB 隐藏标签会冻结 rAF/transition/合成器（"加载屏卡死"伪象，查 leave-active 类判定）；文件上传类 GUI 步骤用 API e2e 补。
8. **集成测试断言实质**："画了像素"而非"文件存在"；跨函数契约锁字面值。
9. **工具**：审查以 `ocr delegate`（open-code-review 委托模式，已装 v1.12.7）起手，六步工作法作执行层。
10. **产品语义优先**：连续两轮"修好了但用户仍不满意"→停止修机制，先对齐期望语义（江西合并预览教训）。

---

## 8. 建议的检查执行顺序（第一轮）

1. **谱系对账（约半小时，纯命令）**：现役容器/镜像/卷/env 清单化 → 每个镜像 tag 对应 git commit？`docker diff` 列可写层资产 → 逐项标注"重建后丢失风险"。§1 全节。
2. **P0 六项快查**：§1.1–1.6 对照送测 6 个 P0 逐项过（inspect Mounts / ps aux worker / 上传落点反查 / 数据混线扫描）。
3. **API 扫描**：§2 的 grep 清单（Cache-Control / timeout / MAX_CONTENT_LENGTH / 路径参数路由）。
4. **worker 生命周期代码审查**：§4.1/4.4/4.5（EPIPE 隔离、取消 e2e、孤儿收口）。
5. **安全四件套**：§5.1–5.4。
6. **前端运行时静态扫**：§3.1/3.2/3.3（compiler-sfc 全量 parse 一次性投入）。
7. **GUI 冒烟**：真实流程跑一遍（登录→上传→任务→结果→删除），console+日志零异常为门。
8. **全量测试门**：按 §7.6 的权威跑法在容器内跑，避免 PATH/开关/GPU 假红。

---

*生成：ZCode 会话，2026-11-04（北京时间）。江西侧证据均可在 JiangXi-Platform 仓库 docs/ 与 AGENTS.md §7 复核。*
