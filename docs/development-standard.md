# YunNan 后续开发规范：模块、性能与测试

适用对象：本项目的两名开发者、后续新生和更换后的 Agent。目标是每次改造范围清楚、能独立调试、普通规模运行顺畅、问题可复现。

本规范约束后续开发；其中性能数字是拟定验收目标，尚不是甲方机器上的实测结果。规则入口为 [AGENTS.md](../AGENTS.md)，新人操作示例见 [新生与 Agent 协作手册](agent-collaboration-guide.md)，测试结果填写 [测试报告模板](test_report_template.md)。

## 1. 每次任务保留五件事

1. 写出用户现在遇到什么问题、完成后能做什么。
2. 指定一个主模块，读清调用者、输入、输出和数据写入位置。
3. 做能解决问题的最小改动，说明是否改变接口、存储或部署。
4. 跑对应测试；有页面交互就实际操作，有性能影响就比较前后耗时。
5. 交付 diff、验证结果和未完成项，让另一个人能接着做。

文案和小缺陷修复用几句话说明即可；跨模块功能才需要短计划与接口示例。已授权的普通实现和验证直接推进。只有产品选择不明确、范围明显扩大、覆盖他人改动或涉及未授权高风险操作时才需要停下确认。

不以文件数量、抽象层数量、测试数量或代码行数衡量完成度。不强制每次新增接口版本、审批节点、兼容层、迁移脚本或独立计划文件。

## 2. 模块边界与低耦合

DTO 指接口约定的数据对象；fixture 指可反复使用的小型测试样例。它们帮助双方在不运行对方全部服务的情况下开发。

| 模块与实际位置 | 负责什么 | 对外边界 / 独立验证 | 不应混入的职责 |
| --- | --- | --- | --- |
| Miner 工作台与地图：`miner/src/` | 项目选择、资产展示、任务入口、地图浏览 | 公开项目 DTO、组件参数与事件；fixture 和 Node 测试 | 数据库查询、模型调用、服务器物理路径、重复计算就绪规则 |
| Miner BFF：`miner/server.js`、`miner/routes/`、`miner/services/` | 浏览器与 Flask 间的会话转发、同源接口、必要适配 | HTTP 请求/响应；替换后端 client 测试 | GIS 运算、项目业务状态计算、直接拼文件输出 |
| 项目领域：`backend/applications/project_hub/` 的项目与读模型代码 | 项目生命周期、概览、资产、活动 | Project API、公开服务入口；临时数据库测试 | 加载权重、在概览请求中执行推理 |
| 空间资源：`project_hub/spatial_*` | 矿山边界、底图登记、离线切片、激活、地图 manifest | 空间资源/任务 DTO；合成矢量与小 TIFF | 修改分类成果版本、改原始影像内容 |
| 推理：`backend/applications/inference/`、`backend/applications/kml_roi/` | 固定模型任务、分块处理、标签与暂存输出 | 任务 DTO、标签/暂存输出约定；mock runner、小数据运行 | 在线训练、由模型代码直接修改项目工作流规则 |
| 成果：`project_hub/classification_results.py`、`project_hub/inference_results.py`、矢量化代码 | 标签矢量化、成果发布、人工修订、当前成果导出 | 成果 API、版本号、GeoJSON；小标签栅格与临时库 | 重训模型、改输入影像、覆盖自动基线 |
| GeoView：`frontend/src/` | 解译页面、推理后分类矢量编辑 | Flask 成果与瓦片 API；编辑 payload 测试、浏览器操作 | 搬到 Miner 重建第二个编辑器、在前端决定正式版本 |
| 部署：`docker/`、Compose、离线脚本 | 进程、挂载、镜像、健康检查与恢复 | 独立测试环境和部署检查脚本 | 业务规则或分类类别调整 |

Miner 经 BFF 访问 Flask；GeoView 沿用自己的 API client 访问 Flask。后端同进程模块可以调用明确的服务函数，不要求为低耦合拆成微服务或在进程内绕 HTTP。

低耦合在评审中检查四点：

- 改显示文案或页面布局，无需改数据库、推理代码；改推理执行细节，无需改项目卡片。
- 一个业务判断有一个所有者。例如项目是否可推理由后端 capability 决定，前端只控制展示和导航。
- 能替换模块的外部依赖完成测试；mock 的是 HTTP、runner 等边界，不是把被测业务函数替换成固定成功。
- 对外依赖没有偷偷变成底层表名、服务器路径、组件私有状态。公共入口有真实调用者，避免空壳 service 层。

详细状态字段以 [项目架构契约](architecture/project-structure-and-low-coupling-contract-v1.md) 为准；矢量类别、坐标、版本和保存约束以 [矢量成果契约](vector-result-v1-contract.md) 为准。本规范不复制完整字段表。

## 3. 两个人按模块推进，如何独立调试

项目负责人确定需求、优先级、跨模块契约和交付范围，最终由项目负责人合并 `main`。每项任务指定一个主模块负责人；模块实现和技术复核可以轮换，但这不转移最终合并权。例如一人负责项目/空间/成果后端，另一人负责 Miner/GeoView；推理和部署单独排任务，不要求两人同时跨所有模块。

协作者可自主修改任务卡范围内的模块内部逻辑、样式和对应测试。尚未授权的公共接口、状态、持久化格式、算法/空间规则、共享依赖或部署配置变化先同步。任务卡、学习要求和通知方式见 [协作指南](agent-collaboration-guide.md)。

| 情况 | 提供方先给什么 | 消费方如何先开发 | 汇合时验证什么 |
| --- | --- | --- | --- |
| 新增资产展示 | 成功、空、错误 fixture，字段含义 | 给 API client 注入假 HTTP 或给组件传 DTO | 真后端返回形状、项目切换、失败提示 |
| 矢量编辑保存 | 成果、保存 payload、409/422 示例 | 小 GeoJSON 调试选中、改类、编辑、保存参数 | 真正落盘/入库后重新读取；冲突不能覆盖 |
| 推理任务入口 | 创建与轮询 DTO、终态示例 | mock queued/running/succeeded/failed，验证页面反馈 | 小 TIFF 加真实 worker 跑完，再读成果 |
| 离线切片 | 候选底图、任务与 manifest 示例 | mock 进度和失败状态调试恢复入口 | 小底图真正生成瓦片、激活并在浏览器显示 |

现有可复用样例在 `tests/fixtures/project-hub-v1/`。参考 `miner/test/projectWorkspaceApi.test.js` 的 HTTP 注入，及 `frontend/test/classificationEditor.test.mjs` 的编辑数据测试。需要展示 mock 页面时仅用于开发/测试，不能把假数据作为正式页面的错误降级。

两个人或两个同时写代码的 Agent 使用各自分支和工作目录；只读审查可以共用目录。共享数据时权重、原始影像只读；可写的测试数据库、项目输出和端口分别隔离。同一工作目录即使换了分支，文件也不是彼此独立的。

同一文件同一时间由一个人主改。另一人需要改它时先同步接口与修改区域。小型前后端联动可以在一个 PR 内交付，以一个业务目标为边界；能独立合入的较大功能再拆 PR。

## 4. 源码与大文件分开管理

- Git 保存源码、锁文件、配置示例、小型合成 fixture、测试与文档。
- 权重、镜像 tar、原始 TIFF、瓦片、运行输出、数据库卷、真实密钥不提交 Git。
- 使用现有离线部署方案共享运行资产；交接记录文件标识、大小、SHA-256、镜像 ID/digest 和挂载用途即可，不新建资产平台。
- 后续地物分类流程沿用离线放置影像、登记数据集、选择项目影像。推理运行中不要覆盖或移动登记影像。
- 推理完成时间与数据大小、像素数、ROI、波段、模型、设备有关；文件格式扩展和数据预处理变化必须单独评估输入一致性，不因“读取成功”就承诺分类精度。
- 原始影像、自动分类成果与人工修订分别保存。人工编辑形成新版本，不能悄悄回写原始标签或把历史统计冒充已按新矢量重新计算。

当前继续沿用 Vue；在线训练、模型重训、React 重写、浏览器 GB 级上传/切片和多人实时编辑不属于本期范围。

## 5. “中等速度”的可测定义

### 5.1 固定测试条件与数据规模

首次性能验收选一台固定参考机，记录 CPU、内存、GPU 型号及显存、磁盘、系统、浏览器、Docker 分配资源和网络。甲方 4070 是待验硬件，不能只凭显卡型号推导整机性能。

准备下列三档数据；这是后续测试集要求，不代表仓库已备齐这些数据：

| 档位 | 数据要求 | 用途 |
| --- | --- | --- |
| 小样例 | 2 个项目，数个矿山，带 CRS/NoData 的小 TIFF，空/失败/正常成果 | 日常功能、隔离与恢复验证，无需完整客户数据 |
| 常规档 | 50 个项目；目标项目 100 个矿山、100 条资产；单次编辑 500 个面要素、20,000 个坐标位置；1 GiB 级代表性 TIFF | 常规页面、保存、查询性能；TIFF 另记像素、波段、压缩和 ROI，不只记文件大小 |
| 边界档 | 接近现有编辑容量的有效数据、超过上限的数据；接近交付规模的大底图 | 验证容量、提示、内存和恢复；不要求日常每次运行 |

当前人工保存上限已在成果实现与契约中定义：10 MiB 请求、2,000 个要素、单要素全部环合计 5,000 个坐标位置、总计 100,000 个坐标位置；应同时满足。一个 MultiPolygon 要素可以包含多个面，容量按 Feature 计数。它们是现有容量约束，**不是流畅性证明**，也不能用调低上限掩盖回归。

### 5.2 初始验收目标

下表针对参考机、本机或稳定局域网、常规档、单人正常操作。页面冷启动与服务预热后的结果分开；GPU 推理额外单列。

| 操作 | 初始目标 | 测量范围 |
| --- | --- | --- |
| 点击、选择、打开弹窗的忙碌/选中反馈 | 150 ms 内 | 用户操作到可见反馈，不把加载占位当结果完成 |
| 常规项目/资产/任务状态读取 | 预热后 p95 ≤ 1 s | 浏览器发出请求到收到响应；不含影像内容下载 |
| 工作台进入或切换项目 | p95 ≤ 3 s | 到正确项目的主要内容可操作；首次冷加载另记，目标 ≤ 5 s |
| 已切片底图进入地图 | p95 ≤ 5 s | 当前视口的主要瓦片和边界可见；不含预先切片时间 |
| 常规矢量选中、改类、顶点操作反馈 | p95 ≤ 200 ms | 操作到画面更新；连续平移缩放大部分时间达到 30 FPS |
| 常规矢量保存后重读 | p95 ≤ 3 s | 从保存到获得新版本并重读一致，不能只测按钮变灰 |
| 推理/切片后台运行期间 | 普通读 API p95 ≤ 2 s | 一项重任务加正常浏览，页面有反馈，任务进度仍可查询 |
| 同一数据的切片/推理吞吐与峰值内存 | 与基线比较，超过 20% 退化需解释 | 冷启动、模型加载、计算、成果发布分别记时；输出质量一致 |

这些数字是首轮待测目标。首次不达标就记录基线、定位主要瓶颈、安排修复，再承诺交付范围。不得把它写成已经达标，也不要求所有尺寸 TIFF 都在几秒完成。

### 5.3 怎么测，什么时候优化

- 页面/API/保存先预热 3 次，再记录至少 20 次操作的耗时、失败数、样本数。p95 为升序样本中第 `ceil(0.95 × N)` 项；20 次只做初筛，临近阈值或波动大再增加样本。
- 用生产构建测页面，浏览器 Network/Performance 记录耗时与长任务；失败也计入报告，不从统计中藏掉。记录冷缓存/热缓存条件。
- 切片/真实模型固定同一输入、权重、ROI、设备和配置，至少跑 3 次，记录逐次耗时和中位数，不用 3 次样本声称 p95。测量完成时包含正式成果发布。
- 用现有进程监视、Docker 统计和 GPU 工具观察峰值资源；一次重任务运行时检查普通页面。连续打开/关闭地图或编辑器 20 次，观察内存是否持续上涨及监听器、轮询是否释放。
- 只有影响列表、序列化、SQL、地图渲染、矢量计算、重任务或部署资源的改动需要前后比较。纯文档、文案无需做性能测试。

优化先查重复请求、重复数据库查询、全量扫描、大 JSON、重复初始化地图和主线程计算。按测量结果采用批量查询、已有筛选/分页能力、按需加载、局部刷新或分块读取；需要新分页契约时连同消费方一起改。缓存必须说明何时失效。

不为未来并发预先增加 Redis、消息中间件或微服务。GPU 并发沿用现有配置，增加前先量显存；大底图走离线目录与后台切片。正式矢量不能为了流畅而静默简化；若使用仅展示的简化数据，须保证保存仍使用完整几何并独立验收。

## 6. 测试按风险选择

本节回答「选什么测、跑什么命令」。想不出测试思路时的检查单、失败定责方法、以及外部测试技巧的采纳标准与积累记录，见 [测试方法手册](testing-playbook.md)。

### 6.1 每类改动的最小验证

| 改动 | 必须验证 | 需要时再补 |
| --- | --- | --- |
| 纯文档/提示文案/小样式 | 文档链接、拼写、diff；界面文案/样式实际查看 | 样式触及布局时跑对应前端构建；无需为文字写单测 |
| UI 逻辑/导航/弹窗 | 对应前端测试与构建；成功、空、失败、项目切换的浏览器操作 | 改复杂异步交互时加组件或浏览器回归 |
| BFF/API/DTO | 相关接口测试；成功、空、参数错误、未登录、跨项目、后端失败；消费方测试 | 保存或发布链加临时数据库/文件验证 |
| 业务规则/存储/修订 | 单元及集成测试；修改后重新读取；失败不留下部分结果 | 并发更新、部署用数据库上的事务行为 |
| GIS/矢量化 | CRS、经纬度顺序、范围、NoData、类别、连通分量、碎斑边界、几何有效性 | 与同输入基线比较面积/要素；大数据内存测试 |
| 推理执行或成果发布 | mock runner 的状态/错误处理；同权重小 TIFF 的真实运行 | GPU、CPU 回退、任务取消/重启、吞吐比较 |
| Docker/挂载/部署 | 配置与现有脚本检查；独立环境启动、健康接口、重启与数据保留 | 改离线能力时断网演练；变更恢复机制时恢复副本 |

Bug 优先先复现，再修复，再证明原场景正常。新增断言应检查用户可见行为、持久化或数据约束，不只是搜索源码包含某字符串。测试自己生成数据库和目录，不连接甲方库、不用真实管理员密码。

### 6.2 功能联调必须有的路径

| 场景 | 关键断言 |
| --- | --- |
| 建项目 → 自定义 FID 预览 → 导入 | 预览允许选字段；导入后的 `FID_1`、地图和矿山引用一致 |
| 底图登记 → 切片 → 激活 → 地图 | 真瓦片可显示；坏底图失败后能换文件重试，不被历史失败锁住 |
| 登记影像 → 创建推理任务 → 轮询 → 成果 | 当前项目数据集与年份正确；失败可见；成果能重新读取；无物理路径泄露 |
| 成果 → GeoView → 改类/改边界/增删 → 保存 → 重开 | 项目与 result_id 正确；新版本持久化；自动基线保持不变 |
| 两个页面编辑同一版本 | 后保存者收到 409 且不能覆盖先保存者；未保存内容不能被静默丢弃 |
| 无效几何/越 ROI/越容量/跨成果 ID | 422 或既有归属错误；无部分保存、无额外修订，原版本不变 |
| `ready_empty` 与 `vector_failed` | 合法空成果可编辑；矢量失败有原因且不伪造成空成果，PNG 状态独立 |
| 导出人工修改后的成果 | 下载后实际解析 GeoJSON；内容与服务器当前版本一致 |
| 切项目、重复点击、断网/重连、刷新 | 不串项目、不误重复创建、错误可恢复；刷新后任务及成果以服务端为准 |

项目工作台的资产清单导出与 GeoView 的分类成果导出是不同功能，分别验收。前者返回导出记录不等于用户已经下载成功。

### 6.3 哪些证据不能互相代替

- Node 测试与 Vue 构建通过，不能证明浏览器点击、画图、切项目没有问题。
- mock 推理完成，不能证明真实模型、GPU、权重、挂载或影像波段可用。
- SQLite 测试通过，不能单独证明 MySQL 上的并发事务正确；涉及此类改动需测试部署数据库的副本。
- 同一固定输入输出一致是回归证据；模型精度需要独立标注数据和评价方案，不在本期重训范围内。
- 目前仓库有 Node/Python 测试与小型 fixture；尚不能据此宣称具备完整浏览器 E2E、持续性能基准或全部自动 CI。文档中的待补测试不代表已经实现。

优先补自动化的顺序：矢量保存后重读与 409 → 工作台到推理入口 → 空间任务失败恢复 → 常规档性能记录。先用现有测试和人工演练取得证据，再将反复回归的流程自动化。

## 7. 当前可用的验证命令

下面每段从**当前工作树根目录**开始执行，按任务选取，不需要逐段全跑。依赖已按锁文件安装，Python 使用项目固定 Python 3.10/GDAL 环境或已有运行镜像。先核对本机实际版本和文件存在；`main` 可能尚未包含其他工作分支的实现。

本机没有 Python 环境时，用运行镜像挂载当前工作树执行。注意必须先激活 conda 环境再跑 Python：直接 `--entrypoint python` 启动会缺少 PROJ/GDAL 资源路径，所有 CRS 相关用例（空间预览、底图、shp 导出等）会集体误报失败（2026-09-07 实测：同一代码激活后由 9 处误报转为全绿）。

```powershell
docker run --rm --entrypoint bash `
  -v "<工作树根目录>:/app" yunnan-runtime:current `
  -c "source /opt/conda/etc/profile.d/conda.sh && conda activate MMSeg310 && cd /app/backend && python -B -m unittest test_project_read_models test_project_api -v"
```

两点边界：推理权重与 `miner/yunnan.kml` 等运行资产不入库，跑 `test_inference_runner`、`test_yunnan_project_seed` 需额外只读挂载宿主机对应资产；web 镜像不含 mmseg，也没有 PowerShell 与 docker compose CLI，全量 `discover` 时 `backend/model/custom_models` 会导入失败、若干契约用例跳过，属预期而非回归。

Miner 测试、BFF 语法与构建：

```powershell
Push-Location miner
try {
    npm test
    node --check server.js
    npm run build
} finally { Pop-Location }
```

GeoView 测试与构建：

```powershell
Push-Location frontend
try {
    npm test
    npm run build
} finally { Pop-Location }
```

项目、空间与地图：

```powershell
Push-Location backend
try {
    python -B -m unittest test_project_api test_project_read_models test_project_spatial test_project_map -v
} finally { Pop-Location }
```

安全与数据正确性回归（/static 鉴权、光谱输入收敛、同 fid 多 Placemark、工作簿锁）：

```powershell
Push-Location backend
try {
    python -B -m unittest test_security_recheck test_kml_roi_duplicate_fid test_kml_roi_index_sync_lock -v
} finally { Pop-Location }
```

大文件上传与长时推理（8GB 上传闸门、地物分类 keep_tiff_raw 快速路径、任务超时按影像规模估算、逐 chunk GPU 缓存释放；移植江西 2026-09-19）：

```powershell
Push-Location backend
try {
    python -B -m unittest test_large_tiff_upload test_inference_batch test_inference_runner -v
} finally { Pop-Location }
```

API 层异常处理约定：`except Exception` 分支一律使用 `applications/api/error_responses.py::business_or_server_failure`——业务校验异常（ValueError/FileNotFoundError）回显原文，其余只记服务端日志并返回通用文案 + 500；不得新增 `fail_api(str(exc))` 直回显。

推理入口与发布（含 mock，不代表真实 GPU 验收）：

```powershell
Push-Location backend
try {
    python -B -m unittest test_inference_jobs test_inference_routing test_interpretation_api test_project_inference_results -v
} finally { Pop-Location }
```

矢量化、修订与成果 API：

```powershell
Push-Location backend
try {
    python -B -m unittest test_classification_vectorization test_classification_results test_classification_result_api -v
} finally { Pop-Location }
```

宿主没有 GIS 环境时，可在已安装的运行镜像中运行上述一个测试组。例如以下命令只挂载当前源码，测试使用自己的临时数据，不挂生产卷：

```powershell
$backendSource = (Resolve-Path backend).Path
docker run --rm --entrypoint python -v "${backendSource}:/app/backend:ro" -w /app/backend yunnan-runtime:current -B -m unittest test_inference_jobs test_inference_routing test_interpretation_api test_project_inference_results -v
```

`yunnan-runtime:current` 是 `image_bundle.env` 中的现有标签；报告要记录实际镜像 ID/digest。离线机器缺镜像时按 [离线部署说明](offline_deployment_guide.md) 准备；不要临时升级整套 GIS 依赖。运行每条命令都核对退出码，PowerShell 的 `try/finally` 仅恢复目录，不会自动把所有非零进程退出码变成异常。

仓库没有统一的 `npm run lint` 或 TypeScript 检查脚本，不要编造它们。`node --check` 仅做语法检查；构建日志中的旧 bundle 警告要与新增失败区分。部署相关命令见 [开发接入说明](development_guide.md) 和 [离线部署说明](offline_deployment_guide.md)。

修改推理执行器、设备或 pipeline 时，在后端测试组中补选 `test_inference_runner`、`test_inference_device`、`test_kml_roi_pipeline`。修改部署时，在仓库根运行 `python -B -m unittest test_yunnan_offline_deployment -v`，在后端目录运行 `python -B -m unittest test_inference_image_contract test_inference_worker_app -v`；这些配置测试不代替真实离线启动。

依赖缺失可能使部分 Python 测试被 skip。报告分别写通过、失败、跳过及原因；`OK (skipped=...)` 不等于该组全部场景已经验证。

## 8. PR、复核与交付

一个 PR 解决一个用户问题，说明主模块及关联消费者。接口变更顺序是“约定字段和样例 → 提供方 → 消费方 → 联调”，可以是同一 PR 内的顺序，不要求拆成四轮审批。

PR 最少包含：

```text
问题与结果：用户在什么场景遇到什么问题，现在会怎样。
改动范围：主模块、关键文件、受影响消费者；接口/存储是否改变。
验证：工作树、提交、环境、实际命令、结果；UI/性能证据按需要附上。
未完成：具体场景、原因、影响和下一步。没有执行的写未执行。
回退：可回退的代码提交；若改数据，说明旧版本能否读取和实际恢复方式。
```

模块开发者先自查调用边界、主要 diff 和测试证据，亲自验证相关用户路径，再请求最终评审与合并；需要提前讨论或交接时可先发草稿 PR，明确待验证项。项目负责人做最终评审和合并；Agent 或另一位开发者可以协助技术审查，但不能仅凭 Agent 的“全部通过”合入。协作者与 Agent 不直接推送或自行合并 `main`，不擅自部署。

每个可独立验收的模块任务交付一个聚焦 PR；PR 留完整的范围、验证和限制，日常通知只需“完成模块 + 真实分支名”，必要时附 PR 链接。遇到缺少权限、数据、需求决定或越界影响，及时报告证据和待决定事项，不因“自己搞懂再提交”隐瞒阻塞；未验证的部分不能作为可合并的完成项。

提交前看 `git diff --check`、`git diff --stat` 和逐文件 diff；只暂存本任务文件。提交信息说明用户得到的改进。不要顺手格式化、升级依赖或夹带权重/影像/密钥。

交付明确区分：文档设计、代码已实现、局部测试、真实联调、性能实测、已合并、已部署。没有真实数据/硬件时可以交付代码，但必须保留对应未验收项。失败发生在本次修改之外时记录已有问题，不扩大修复范围。
