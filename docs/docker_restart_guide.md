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

上述标准命令默认使用 CPU，不加载 GPU overlay。只有目标机已配置 NVIDIA 驱动和 NVIDIA Container Toolkit 时，才额外加入 `-f docker-compose.gpu.yml`：

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
