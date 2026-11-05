# 云南环境 Docker 重启与排障

本文只适用于 Compose project `yunnan`。仓库目录示例为 `D:\项目\YunNan`。

当前资源名称：

- 容器：`yunnan-mysql`、`yunnan-backend`、`yunnan-frontend`、`yunnan-miner-api`、`yunnan-miner-web`、`yunnan-inference-worker`、`yunnan-spatial-worker`
- Compose service：`mysql`、`backend`、`frontend`、`miner-api`、`miner-web`、`inference-worker`、`spatial-worker`
- 应用镜像：`yunnan-runtime:current`
- 推理镜像：`yunnan-inference-worker:current`

不要改变 Compose project 名称。以下 Compose 命令都显式使用 `-p yunnan`。

## 1. 标准普通重启

普通重启不需要解析 Compose 变量，优先使用容器命令：

```powershell
Set-Location 'D:\项目\YunNan'

docker restart yunnan-mysql yunnan-backend yunnan-frontend yunnan-miner-api yunnan-miner-web yunnan-inference-worker yunnan-spatial-worker
docker ps --filter 'name=yunnan-' --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
```

确认七个容器均为 `Up`；带健康检查的容器还应显示 `healthy`。

## 2. Compose 重建前置条件

Compose 重建会解析以下秘密变量：

- `MYSQL_PASSWORD`
- `MYSQL_ROOT_PASSWORD`
- `ADMIN_PASSWORD`
- `SECRET_KEY`

这些值必须来自安全来源并可供当前 Compose 进程解析。不得把真实值写入本文、命令历史、工单或日志，也不要运行会展开完整 Compose 配置的命令。

本机已有 `yunnan-mysql` 时，可从该容器的 `Config.Env` 将两个 MySQL 密码仅注入当前 PowerShell 进程。脚本只按变量名提取，不打印变量值：

```powershell
Set-Location 'D:\项目\YunNan'

$mysqlInspect = docker inspect yunnan-mysql | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $null -eq $mysqlInspect) {
    throw '无法读取 yunnan-mysql；必须由运维从安全来源提供 MySQL 凭据。'
}

$containerEnv = @($mysqlInspect[0].Config.Env)
foreach ($key in @('MYSQL_PASSWORD', 'MYSQL_ROOT_PASSWORD')) {
    $prefix = "$key="
    $entry = $containerEnv |
        Where-Object { $_.StartsWith($prefix) } |
        Select-Object -Last 1

    if ([string]::IsNullOrEmpty($entry) -or $entry.Length -le $prefix.Length) {
        throw "yunnan-mysql 中缺少 $key；必须由运维从安全来源提供。"
    }

    Set-Item -Path "Env:$key" -Value $entry.Substring($prefix.Length)
}
```

`ADMIN_PASSWORD` 和 `SECRET_KEY` 仍由 Compose 从现有 `.env` 读取。重建前应确认 `.env` 已由安全来源配置且两项均非空，但不要打印其内容。

如果 `yunnan-mysql` 不存在，不得从历史日志恢复或猜测密码。必须由运维通过凭据管理器等安全来源把 `MYSQL_PASSWORD` 和 `MYSQL_ROOT_PASSWORD` 设置到当前进程，再继续重建。

同时确认 `image_bundle.env` 使用以下云南镜像：

```text
APP_IMAGE=yunnan-runtime:current
INFERENCE_IMAGE=yunnan-inference-worker:current
```

镜像名称不是秘密，但不要在检查时输出同一环境文件中的其他配置。

## 3. Compose 原地重建

完成上一节的秘密变量准备后，原地重建全部 service：

```powershell
try {
    docker compose -p yunnan `
        --env-file .env `
        --env-file image_bundle.env `
        -f docker-compose.prod.yml `
        up -d --force-recreate `
        mysql backend frontend miner-api miner-web inference-worker spatial-worker

    if ($LASTEXITCODE -ne 0) {
        throw "Compose 重建失败，退出码为 $LASTEXITCODE。"
    }
}
finally {
    Remove-Item Env:MYSQL_PASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:MYSQL_ROOT_PASSWORD -ErrorAction SilentlyContinue
}

docker ps --filter 'name=yunnan-' --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
```

上述标准命令默认使用 CPU，不加载 GPU overlay。只有目标机已配置 NVIDIA 驱动和 NVIDIA Container Toolkit 时，才额外加入 `-f docker-compose.gpu.yml`。

> **陷阱（2026-11-05 实咬）**：带 GPU 的机器若用不带 overlay 的标准命令 `up -d`/`up -d inference-worker` 重建，GPU 会被**静默撤掉**——worker 落 CPU 路径后撞启动内存守卫（VM 可用 < checkpoint×2.2 ≈ 4.04GiB）陷入崩溃循环（本机曾连续重启 1067 次，RestartCount 是判别特征）。判别：`docker logs yunnan-inference-worker | grep effective_device` 应为 `cuda:0`；恢复必须带 `-f docker-compose.gpu.yml` 重建。

```powershell
docker compose -p yunnan `
    --env-file .env `
    --env-file image_bundle.env `
    -f docker-compose.prod.yml `
    -f docker-compose.gpu.yml `
    up -d --force-recreate `
    mysql backend frontend miner-api miner-web inference-worker spatial-worker
```

不要添加孤儿容器清理参数，也不要先执行 `down`。单独重建某个 service 时，将最后一行的 service 列表替换为目标名称，例如 `backend`。

## 4. 完整停止与再次启动

完整停止前，先确认第 2 节所述四项秘密变量可从安全来源再次取得。否则一旦容器无法直接启动，将无法安全重建。

仅停止容器，不删除容器、网络或卷：

```powershell
docker stop yunnan-spatial-worker yunnan-inference-worker yunnan-miner-web yunnan-miner-api yunnan-frontend yunnan-backend yunnan-mysql
```

再次启动时先启动 MySQL，再启动应用容器：

```powershell
docker start yunnan-mysql
docker start yunnan-backend yunnan-frontend yunnan-miner-api yunnan-miner-web yunnan-inference-worker yunnan-spatial-worker
docker ps --filter 'name=yunnan-' --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
```

如果容器不存在或直接启动失败，停止继续尝试，按第 2、3 节准备变量并原地重建。

## 5. 非破坏性排障

### 5.1 容器名称或 Compose project 异常

只读检查容器及其 Compose 标签：

```powershell
docker ps -a --filter 'name=yunnan-' --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
docker inspect --format '{{.Name}} project={{index .Config.Labels \"com.docker.compose.project\"}} service={{index .Config.Labels \"com.docker.compose.service\"}}' yunnan-backend
```

project 应为 `yunnan`，service 应为本文开头列出的名称。不要用强制删除容器来处理名称冲突；先确认实际 project、service、挂载和数据卷，再决定处置方式。

### 5.2 `config.yaml` 挂载错误

报错包含 `/app/config.yaml ... not a directory` 时，仅做只读检查：

```powershell
Get-Item 'D:\项目\YunNan\config.yaml' | Format-List FullName,PSIsContainer,Length
docker inspect --format '{{range .Mounts}}{{println .Source \"->\" .Destination}}{{end}}' yunnan-backend
```

宿主机 `config.yaml` 必须是文件，且挂载目标必须与 Compose 定义一致。不要直接删除或覆盖该路径；确认异常来源后再修复。

### 5.3 灰底图或瓦片 404

```powershell
curl.exe -I http://127.0.0.1:5008/api/auth/session
curl.exe -I http://127.0.0.1:8000/api/auth/session
```

先在 4000 登录，再用已认证浏览器网络面板（或携带会话 Cookie 的请求）访问完整 manifest 端点 `GET http://127.0.0.1:8000/api/projects/{projectId}/map/manifest`，读取真实的 `projectId`、`resourceId`、`z/x/y`，将所有占位符替换为实际值后验证 `http://127.0.0.1:8000/tiles/projects/{projectId}/{resourceId}/{z}/{x}/{y}.png`。底图链路排查的完整流程见 [`docs/offline_deployment_guide.md`](offline_deployment_guide.md)。

### 5.4 精简日志检查

```powershell
docker logs --tail 100 yunnan-backend
docker logs --tail 100 yunnan-inference-worker
```

日志只在本机查看。对外提供前必须移除凭据、Cookie、请求正文和本地敏感路径。

## 6. 明确禁止的操作

除非有经过确认的数据销毁方案，否则禁止执行：

```text
docker compose down -v
docker volume rm ...
docker image rm ...
docker system prune ...
```

排障和重建不得删除或清空 `yunnan_*`、旧环境数据卷、数据库或镜像。

## 7. 分组重启时序与 502 窗口（2026-10-27 实测补充）

- 只重启部分容器时，**miner-api 必须与 backend 一起重启**（或在其后）：backend 重启需约 40s（等 MySQL + 初始化），期间 BFF 放行会得到 502、前端登录卡"登录中"。BFF 已内置连接级单次重试与登录 502 中文文案兜底；`docker compose up -d` 路径已用 `service_healthy` 条件依赖。
- 前端两容器（frontend/miner-web）入口在启动时执行 `npm run build` 重建 dist：重启后约 1–3 分钟才可访问；磁盘高负载时可达十分钟级。重启前端容器后请以 `curl http://localhost:3000/`、`curl http://localhost:4000/` 确认再验收。

## 8. 前端热更纪律（2026-11-05 迁移审查 P1-6，江西 3.4 同款教训）

当前前端两容器是"bind 主树源码 + 启动时 `npm run build`"模式，由此固定两条纪律：

1. **禁止 `docker cp dist` 式热更前端**。启动脚本每次重启都会从源码重新构建 dist，任何 cp 进去的产物都会在下次重启被静默冲掉——江西多个修复曾因此"从未生效"。前端改动一律改主树源码（`frontend/src`、`miner/src`，容器只读 bind），重启对应容器生效。
2. **重启前端容器 = 重新构建**：1–3 分钟不可用（高磁盘负载十分钟级），且构建失败（依赖缺失/OOM）会让容器起不来。重启前确认主树 `npm run build` 可通过；重启后按 §7 的 curl 两端口确认再验收。

另注意镜像谱系（P1-5）：`yunnan-runtime:current` 镜像内的 dist 只反映**镜像构建时点**的主树，与运行容器实际服务的版本无关。送外测/离线打包前必须按 `docs/offline_deployment_guide.md` 重建镜像，禁止直接复用开发机上的旧 tag。

## 9. 宿主内存与 CPU 兜底限制

- 推理 checkpoint（fp32 约 1.97GB）以 CPU 加载需宿主可用内存 ≥ 约 4.4GB（体积 × 2.2）。**宿主总内存 < 8GB 时，GPU 模型常驻后强制 `device=cpu` 的任务会在加载前被直接拒绝**（错误码 `INSUFFICIENT_HOST_MEMORY_FOR_CPU`），GPU 正常路径不受影响。这是设计行为（R1 归因体系的预检分支），扩内存或释放常驻后自愈。
- 部署机内存底线与建议见 `docs/design/design-suggestions-2026-11.md` D9（≥8GB，推荐 16GB）。

## 10. Docker Desktop（开发环境）宿主挂载冻结处置

开发机（Docker Desktop + Windows bind mount）在 11GB 级底图复制与其他高 I/O 并发时，可能出现引擎 API 500、`docker` CLI 挂起、宿主挂载路径卡死而宿主磁盘本身空闲——这是 VM 挂载通道死锁，**不是磁盘瓶颈**。处置（2026-10-27 实测有效）：

1. `wsl -t docker-desktop` 单独终止**不能**干净恢复（引擎 500 持续），不要止步于此；
2. 完整重启 Docker Desktop：`Stop-Service com.docker.service` → 结束 `Docker Desktop`/`com.docker.backend` 进程 → 重新启动 `Docker Desktop.exe`；
3. 七个容器均为 `restart: unless-stopped`，引擎恢复后自动拉起；进行中的底图复制任务会被 worker 恢复逻辑标记失败并**从头重拷**（部分 `.copying` 会被清理），完成后用空间任务 retry 接口重切即可；
4. 生产裸机部署无宿主挂载耦合层，不受此问题影响；若交付环境复现同类症状，优先检查存储直通性能而非数据损坏。

## 11. 生产化运维基线（2026-11-05 加固批）

- **版本对账**：重启/送测前建议 `export YUNNAN_GIT_SHA=$(git rev-parse --short HEAD)` 再 `docker compose up -d`，之后 `curl http://localhost:5008/api/system/version` 应返回该 sha——不再靠 dist 文件日期猜版本。
- **请求编号**：所有后端响应带 `X-Request-Id` 回显（前端自动注入 `fe-*` 前缀）；报障让用户给"编号"，后端 5xx 日志按同 ID 检索。
- **日志轮转**：全部容器 json-file 已限 20m×3；MySQL 慢查询(>2s)落 `yunnan_mysql_data` 卷内 slow.log。
- **内存上限**：backend 3G / inference-worker 4.5G / mysql 1.75G / spatial-worker 2G / 前端类 768m / miner-api 512m——单容器失控不再拖垮全栈。
- **备份**：`bash docker/backup_yunnan.sh`（详见 docs/backup-restore.md），每周与送测前必做。
- **登录限速**：同一账号 15 分钟内 5 次失败锁定（进程内存计数，重启清零）。
