# 云南与江西运行资源隔离设计

## 背景与问题

当前云南部署的 Miner 从 `miner/yunnan.kml` 加载 565 座云南矿山，但 Docker Compose 将 MySQL、上传文件、解译结果等运行数据挂载到通用的 `geoview_*` 数据卷。现有 `geoview_mysql_data` 中唯一的项目记录来自 `system_seed:jiangxi_mine_csv`，项目区域为江西省并绑定 284 座江西矿山。因此项目工作台同时展示了江西项目元数据和云南可选矿山，造成省份、数量与成果来源混用。

界面中的“绑定矿山”列表没有最大高度和纵向滚动规则，565 条选项会持续拉长页面，用户无法在固定区域内完整浏览。

## 目标

- 保留现有江西镜像和 `geoview_*` 数据卷，不删除、不覆盖。
- 云南部署使用独立且可识别的镜像标签、容器名和数据卷名。
- 云南数据库只初始化云南项目，默认绑定 `yunnan.kml` 中全部 565 座矿山。
- 云南项目监测期依据现有指数工作簿设置为 2017–2025。
- 云南后续上传、解译、索引、瓦片和数据库数据只写入云南数据卷。
- “绑定矿山”区域具有明确的最大高度和纵向滚动条。

## 非目标

- 不删除或迁移江西镜像、数据库、数据卷和历史成果。
- 不尝试把当前共享卷中无法可靠判断省份的历史图片或解译结果自动归类。
- 不改造项目数据模型，不新增“省份命名空间”字段。
- 不改变登录、API 格式、端口和现有前端路由。

## 方案

### 1. Docker 资源隔离

云南 Compose 资源采用云南专用名称：

- 应用镜像标签：`yunnan-runtime:current`
- 推理镜像标签：`yunnan-inference-worker:current`
- 容器名称：`yunnan-backend`、`yunnan-frontend`、`yunnan-miner-api`、`yunnan-miner-web`、`yunnan-inference-worker`、`yunnan-mysql`
- 数据卷：`yunnan_mysql_data`、`yunnan_backend_static`、`yunnan_miner_outputs`、`yunnan_miner_tiles`、`yunnan_miner_uploads`、`yunnan_inference_runtime`、`yunnan_hf_cache`

第三方 MySQL 基础镜像不包含项目数据，可继续使用现有官方镜像标签。现有 `geoview-jiangxi:gpu`、`geoview-runtime:*` 和 `geoview_*` 数据卷全部保留。为避免重复构建大型镜像，可将当前已验证的云南运行镜像增加云南专用标签；Compose 只引用云南标签。

当前占用 3000、4000、5008、8000 端口的 `cugrs-*` 容器在切换时由云南命名的新容器替代。旧数据卷不随容器替换删除。
后端健康检查使用现有的 `/api/auth/session` 接口，避免根路径返回 404 导致容器被误判为不健康。

### 2. 云南项目初始化

在新的云南 MySQL 数据卷完成表结构初始化后，运行一次幂等的云南项目初始化逻辑：

- 项目名称：`云南矿山生态修复监测项目`
- 区域：`云南省`
- 状态：`active`
- 负责人：`admin`
- 监测开始年份：`2017`
- 监测结束年份：`2025`
- 备注：`system_seed:yunnan_kml`

初始化逻辑解析 `/app/miner/yunnan.kml`，以有效且唯一的 `FID_1` 为主键，写入项目矿山绑定及名称、州市、面积、治理状态快照。预期有效绑定数为 565。脚本仅在云南项目不存在时创建；重复执行时更新缺失快照并保持单一项目，不创建重复绑定。

不从 `geoview_mysql_data` 复制江西项目，也不复制 `geoview_backend_static` 和 `geoview_miner_outputs` 中无法确认归属的历史成果。旧卷保留，后续仍可由江西部署单独挂载。

### 3. 绑定矿山列表滚动

保持现有列表结构，只调整 `.mine-list` 样式：

- 最大高度为 `min(60vh, 640px)`。
- 设置 `overflow-y: auto`。
- 使用稳定的滚动条占位并增加少量右侧内边距，避免内容被滚动条遮挡。
- 移动端继续使用相同的纵向滚动行为。

不增加分页、虚拟列表或搜索功能，避免扩大本次修改范围。

## 数据流与边界

1. Miner API 读取工作区内只读挂载的 `yunnan.kml` 和云南指数工作簿。
2. 项目元数据和绑定关系写入 `yunnan_mysql_data`。
3. 上传文件写入 `yunnan_miner_uploads`，解译结果写入 `yunnan_miner_outputs` 和 `yunnan_backend_static`。
4. 江西部署继续使用原有江西镜像和 `geoview_*` 数据卷；两套运行资源没有共享的项目数据写入点。
5. 模型、KML、工作簿和代码来源由 Compose 的云南工作区挂载确定，不从江西卷读取。

## 错误处理与安全

- 初始化前校验 KML 文件存在，并校验解析后的有效唯一矿山数为 565；数量不符则失败退出，不写入不完整项目。
- 新卷启动失败时保留旧 `geoview_*` 数据卷，允许恢复到切换前状态。
- 不输出数据库密码、管理员密码或密钥。
- 不执行数据卷删除、镜像删除或数据库清空操作。

## 验证标准

- `docker volume inspect` 显示云南容器只挂载 `yunnan_*` 数据卷。
- 现有 `geoview_*` 数据卷和 `geoview-jiangxi:gpu` 镜像仍存在。
- `GET /api/projects` 只返回云南项目，区域为云南省，矿山数量为 565，监测期为 2017–2025。
- `GET /api/projects/<id>` 返回 565 条云南矿山绑定，不包含江西项目备注或江西省快照。
- Miner 项目工作台不再显示“江西”或 `system_seed:jiangxi_mine_csv`。
- `.mine-list` 的 `scrollHeight` 大于 `clientHeight`，计算样式 `overflow-y` 为 `auto`，可滚动查看末尾矿山。
- Miner 测试、后端初始化测试和生产构建通过，云南容器健康。

## 回滚

停止云南命名容器后，可使用原 Compose 配置或江西部署重新挂载未修改的 `geoview_*` 数据卷。由于本方案不删除旧镜像与旧卷，回滚不依赖数据恢复操作。
