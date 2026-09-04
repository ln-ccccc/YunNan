# 云南项目 Windows 完整离线迁移设计

## 1. 背景与目标

当前 `YunNan` 目录已包含源码、模型、项目空间数据、大理离线底图和三个 Docker 镜像归档，但 Docker 命名卷仍保存在当前电脑的 Docker Desktop 中，现有 Bash 部署脚本也不能直接作为 Windows 部署入口。

本次交付目标是将现有系统完整迁移到另一台 Windows x86_64 电脑，在 Docker Desktop 的 Linux 容器模式下离线部署，并保留当前数据库、用户账号、上传文件、推理输出、缓存和历史任务数据。

## 2. 目标环境与前提

- 目标系统：Windows x86_64。
- 容器环境：Docker Desktop，使用 Linux 容器。
- 运行模式：默认 CPU；不要求 NVIDIA GPU。
- 磁盘空间：复制前至少预留 100 GB；实际要求以最终清单中的目录大小为准。
- 端口：宿主机 `3000`、`4000`、`5008`、`8000` 可用。
- 安全：根目录 `.env` 包含账号和密码，整个离线目录按敏感数据保管。

## 3. 交付形态

保持现有目录结构，不额外生成单个压缩包。关键交付内容如下：

```text
YunNan/
├── backend/
├── frontend/
├── miner/
├── project_storage/
├── maps/
├── docker/
├── images/
│   ├── yunnan_runtime_current.tar
│   ├── yunnan_inference_worker_current.tar
│   ├── mysql_8.0.30-8.6.tar
│   └── SHA256SUMS
├── volumes/
│   ├── yunnan_mysql_data.tar
│   ├── yunnan_backend_static.tar
│   ├── yunnan_hf_cache.tar
│   ├── yunnan_miner_outputs.tar
│   ├── yunnan_miner_uploads.tar
│   ├── yunnan_inference_runtime.tar
│   ├── yunnan_miner_tiles.tar
│   └── SHA256SUMS
├── docker-compose.prod.yml
├── docker-compose.gpu.yml
├── deploy_offline.ps1
├── deploy_offline.sh                 # 已禁用的旧 Linux 入口
├── HOST_SHA256SUMS
├── image_bundle.env
└── .env
```

`yunnan_miner_tiles` 已不再由当前 Compose 挂载，但仍导出和恢复，确保迁移包完整保存当前 Docker 状态。该旧瓦片卷不参与当前运行。

## 4. 数据一致性策略

数据卷导出前，全部 `yunnan-*` 容器必须停止。这样可以避免 MySQL、上传文件和任务状态在导出期间继续变化。

导出流程只读取现有命名卷，不删除或修改卷内容。每个卷单独生成 tar，随后计算 SHA256。导出完成后容器保持停止状态，恢复到本次操作开始时的状态。

`project_storage/`、`backend/model/` 和 `maps/` 是宿主机目录，直接随项目目录复制，不重复放入数据卷归档。根目录 `HOST_SHA256SUMS` 覆盖当前 Compose 使用的全部宿主机绑定源，包括配置、启动脚本、后端与前端源码、Miner 固定输入、`project_storage/`、`maps/dali/` 和模型文件。清单使用相对路径、固定排序和 SHA256；不包含已有独立清单的镜像/卷归档，也不包含含凭据的 `.env`。

目标机预检必须将清单条目与当前允许的宿主绑定文件集合做精确匹配：拒绝缺失、重复、额外或逃逸根目录的条目，并重新计算每个文件的 SHA256。成功时只输出汇总数量，不逐项打印底图或项目数据文件名；失败时报告相对位置并要求重新复制。当前宿主清单包含 42,644 个文件、40,303,497,443 字节（37.536GiB），预检会全部读取，目的是发现跨电脑复制中的遗漏或截断。

## 5. Windows 部署脚本

新增根目录 `deploy_offline.ps1`，使用 Windows PowerShell 兼容语法，不依赖 WSL、Git Bash、Python、Node.js 或联网下载。

脚本执行顺序：

1. 检查 Docker CLI、Docker Compose 和 Linux 容器引擎是否可用。
2. 检查必需文件、目录、镜像 tar、卷 tar、宿主绑定源和 `.env` 必需变量，但不输出变量值。
3. 校验 `images/SHA256SUMS`、`volumes/SHA256SUMS` 和根目录 `HOST_SHA256SUMS`；宿主清单与实际允许文件集合必须完全一致。
4. 检查端口占用和目标机上的 `yunnan-*` 容器、`yunnan_*` 命名卷冲突。
5. 作为必需文件校验，扫描 `maps/dali/`，按 `.tif`/`.tiff` 扩展名发现至少一个地图文件；文件名和编码不作为契约。
6. 加载三个镜像，并核对预期镜像标签。
7. 创建七个云南卷，每卷使用随机 `yunnan.offline.restore=<runId>` 所有权标签，核对标签归属后通过显式 `--entrypoint /bin/sh`、`volume-nocopy` 和 `tar -xpf` 从对应 tar 恢复数据。
8. 使用根目录 `.env` 执行 `docker compose --env-file .env -f docker-compose.prod.yml up -d`。
9. 在 360 秒内等待七个固定容器全部为 `running`；MySQL、Backend、Frontend、Miner API、Miner Web 还必须为 `healthy`，Inference Worker 和 Spatial Worker 至少保持运行。随后验证四个入口及推理运行时。
10. 输出服务地址与容器状态；失败时输出清晰错误。

脚本提供 `-ValidateOnly` 参数，只执行环境、文件、三类 SHA256 清单、端口和冲突检查，不加载镜像、不创建卷、不启动容器。验证模式将同名云南容器或卷报告为警告，以便源电脑也能校验迁移包；正常部署遇到任何同名容器或同名卷时拒绝部署并报告，不覆盖目标电脑已有数据。

## 6. 健康检查

部署脚本使用当前有效接口：

- `http://127.0.0.1:3000/`
- `http://127.0.0.1:4000/`
- `http://127.0.0.1:5008/api/auth/session`
- `http://127.0.0.1:8000/api/auth/session`
- 容器内 `/app/docker/check-inference-runtime.py`

HTTP 可用不能替代容器状态门禁。部署成功还要求七个固定容器全部处于 `running`；定义了 healthcheck 的五个服务必须达到 `healthy`，防止 MySQL 异常或 Spatial Worker 退出时脚本误报成功。

不再使用已返回 `410 Gone` 的全局 `/api/stats` 和 `/tiles/{z}/{x}/{y}.png`。项目地图在登录后通过项目 manifest 与项目级瓦片地址验收。

## 7. 当前机验证

打包前执行以下验证：

1. 使用根目录 `.env` 启动当前七个服务。
2. 确认 Compose 健康状态、四个 Web/API 入口和推理运行时。
3. 核查项目 1 的 manifest、项目空间文件和至少一个现有项目级瓦片。
4. 运行现有离线部署契约测试、Miner 测试和前端 URL 测试。
5. 停止全部云南容器后导出数据卷。
6. 校验镜像、卷归档及宿主绑定数据 SHA256。
7. 将七个卷归档恢复到带唯一前缀的临时验证卷，确认七个归档均非零且可读取；恢复后的条目、内容、uid/gid/mode 与源卷精确一致；源卷为空时允许空归档并记录，然后删除仅由本次验证创建的临时卷。
8. 执行 `deploy_offline.ps1 -ValidateOnly`，确认三类清单通过且控制流在任何写操作前退出。
9. 契约测试明确锁定 `-ValidateOnly` 退出点早于镜像加载、卷创建、恢复容器和 Compose 启动，并锁定七容器/五健康服务的成功门禁。

当前电脑无法在不复制全部数据卷的前提下模拟一台完全干净的 Docker Desktop，因此最终端到端恢复仍需在目标电脑执行一次。当前机验证负责确认源系统可运行、所有归档可读、部署脚本输入完整且不会覆盖已有资源。

## 8. 错误处理与安全边界

- 任何必需文件、三类校验值、镜像标签、端口或 Docker 前提不满足时立即停止。
- 不使用 `docker compose down -v`，不删除现有命名卷。
- 不自动覆盖目标电脑已有同名容器或卷。
- 日志不得输出 `.env` 的密码或会话密钥。
- 部署失败时保留已创建的容器和卷供排查，不做自动破坏性回滚。
- 临时验证卷使用按归档卷精确映射的 `yunnan_verify_` 名称，并附加本次流程随机生成的 `yunnan.offline.restore=<runId>` 所有权标签；只删除本次流程创建且已核对名称和标签的临时卷。

## 9. 验收标准

- 当前机七个服务实际启动并通过定义的健康检查。
- 三个镜像归档标签正确且 SHA256 匹配。
- 七个当前数据卷均有归档且 SHA256 匹配。
- `HOST_SHA256SUMS` 与全部 Compose 宿主绑定输入精确一致，所有文件 SHA256 匹配。
- 七个归档均非零且可读取；恢复后的条目、内容、uid/gid/mode 与源卷精确一致；源卷为空时允许空归档并记录。
- `deploy_offline.ps1 -ValidateOnly` 在完整目录上返回成功。
- 正式启动必须等待七容器全部运行且五个带健康检查的服务为健康，不能仅凭 `docker compose ps` 查询成功判定部署完成。
- 文档明确目标机前提、部署命令、验证方式和已知限制。

满足以上标准后，可以将整个 `YunNan` 目录复制到目标 Windows 电脑并执行 Windows 离线部署。首次目标机恢复完成前，不声称已经完成跨机器端到端验证。

## 10. 非目标

- 不升级依赖或重建 Docker 镜像。
- 不调整业务 API、配置结构、目录结构或数据库模式。
- 不增加 GPU 驱动、NVIDIA Container Toolkit 或联网安装流程。
- 不清理旧 `geoview_*` 归档或其他历史文件。
