# GeoView 离线部署指南

> 根目录 `deploy_offline.sh` 已禁用，不得用于完整迁移。Windows 完整迁移只使用 `deploy_offline.ps1`；Linux 仅支持本文下方的手工最小/新部署流程，且不恢复迁移卷。

## Windows 完整迁移包

完整迁移包面向 Windows x86_64，目标机需已启动 Docker Desktop，并使用 Linux containers。目标盘建议至少预留 100GB；不要只复制镜像，必须搬运整个 `YunNan` 目录，才能保留数据库、账号、历史记录、上传文件、模型和离线地图。

当前根目录 `HOST_SHA256SUMS` 固定校验 Compose 使用的宿主输入，包括部署脚本和配置、`docker/`、后端/前端/Miner 源码与模型、`project_storage/`、`maps/dali/` 等，共 42,644 个文件、37.536GiB。它不重复收录 `images/` 和 `volumes/`，这两个目录分别使用自己的 `SHA256SUMS`；根目录敏感文件 `.env` 也不写入宿主清单，但部署脚本会检查其中的必需项。任何已纳入清单的文件发生变化都会使校验失败。

迁移目录还包含 `.env`、`backend/.flaskenv`、数据库卷归档等敏感配置和业务数据。必须通过受控介质或可信链路传输，不要上传到公共网盘或代码仓库。移动硬盘/U 盘应使用 NTFS 或 exFAT，不能使用不支持单个 4GB 以上文件的 FAT32。

从当前电脑复制到空的移动盘目录（示例为 `E:\YunNan`）：

```powershell
$SourceBundle = 'D:\项目\YunNan'
$TransferBundle = 'E:\YunNan'
if (Test-Path -LiteralPath $TransferBundle) {
    throw "目标目录已存在，请改用一个空的新目录：$TransferBundle"
}
robocopy $SourceBundle $TransferBundle /E /COPY:DAT /DCOPY:DAT /R:2 /W:2 /J /XJ
if ($LASTEXITCODE -ge 8) {
    throw "迁移包复制失败，robocopy 退出码：$LASTEXITCODE"
}
```

在目标电脑从移动盘复制到空的本地目录（示例为 `D:\YunNan`）：

```powershell
$TransferBundle = 'E:\YunNan'
$TargetBundle = 'D:\YunNan'
if (Test-Path -LiteralPath $TargetBundle) {
    throw "目标目录已存在，请改用一个空的新目录：$TargetBundle"
}
robocopy $TransferBundle $TargetBundle /E /COPY:DAT /DCOPY:DAT /R:2 /W:2 /J /XJ
if ($LASTEXITCODE -ge 8) {
    throw "迁移包复制失败，robocopy 退出码：$LASTEXITCODE"
}
```

复制完成后，在目标电脑的 PowerShell 中执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
Set-Location -LiteralPath 'D:\YunNan'
.\deploy_offline.ps1 -ValidateOnly
.\deploy_offline.ps1
```

`-ValidateOnly` 是只读预检：逐项计算并核对 42,644 个宿主文件（37.536GiB）、三个镜像归档和七个卷归档的 SHA256，同时检查 Docker Linux engine、Compose、根目录 `.env` 必需项、端口和目标资源。当前三类清单合计会读取 59,026,472,163 字节（54.973GiB，约 55.0GiB）的被校验数据，这不是整个迁移目录的占用大小。机械盘上完整校验可能耗时较长；校验运行期间不要编辑、同步或替换迁移包内的文件。端口占用会使预检失败；只有端口检查通过后，同名资源冲突才在预检中告警且不改动。不加载镜像、不创建卷、不启动服务。

预检成功时会看到十行归档文件名加 `OK`，以及以下两项；只有进程以退出码 `0` 结束才算通过：

```text
Host data files OK (42644 files)
VALIDATION OK
```

正式部署会加载 `images` 中的三个镜像、恢复七个数据卷，并使用项目根目录的 `.env`。`deploy_offline.ps1` 默认只使用 `docker-compose.prod.yml` 的 CPU 模式，不会自动叠加 `docker-compose.gpu.yml` 或启用 GPU。脚本启动 Compose 后，必须确认全部七个云南容器均为 `running`，且 `yunnan-backend`、`yunnan-frontend`、`yunnan-miner-api`、`yunnan-miner-web`、`yunnan-mysql` 五个容器均为 `healthy`，才会继续做 HTTP 就绪检查；`yunnan-inference-worker` 和 `yunnan-spatial-worker` 只要求处于 `running`。状态门禁成功会输出：

```text
All 7 Yunnan containers are running; 5 healthchecks are healthy.
```

如果正式部署失败，不要自行删除、重建或覆盖脚本已创建的云南容器和卷。保留完整 PowerShell 错误输出，由运维结合卷上的 `yunnan.offline.restore=<runId>` 标签核对本次恢复归属后再处理，避免误删已恢复的数据。

正式部署始终拒绝同名的 `yunnan-*` 容器或 `yunnan_*` 卷并报告冲突，不会停止或覆盖这些资源；也不会执行 `down`、删除卷或清理孤儿容器。迁移目录包含数据库账号和会话密钥，必须妥善保管；首次在目标机部署后，仍需按本文档验证章节完成验收。

以下 Linux 流程是最小/新部署：只加载镜像并创建空的运行卷，不恢复完整迁移包中的七个卷，也不保留现有数据库历史。

## Linux 最小/新部署（不恢复迁移卷）

### 1. 目录要求

在项目根目录执行：

```bash
cd /path/to/GeoView
```

确认关键文件存在：

```bash
test -f docker-compose.prod.yml
test -f .env
test -f config.yaml
test -f images/yunnan_runtime_current.tar
test -f images/yunnan_inference_worker_current.tar
test -f images/mysql_8.0.30-8.6.tar
test -d maps/dali
test -n "$(find maps/dali -type f \( -iname '*.tif' -o -iname '*.tiff' \) -print -quit)"
test -f backend/model/mmseg_config/model.inference.pth
```

地图文件按 `.tif`/`.tiff` 扩展名运行时扫描发现；文件名和编码不作为契约。

宿主机 `maps/dali` 由 Compose 只读挂载到 `/project_storage/incoming/dali`；后端从 `project_storage/incoming` 递归扫描地图文件。

根目录 `.env` 含数据库账号和会话密钥，属于敏感文件，必须妥善保管。

### 2. 加载镜像

```bash
docker load -i images/yunnan_runtime_current.tar
docker load -i images/yunnan_inference_worker_current.tar
docker load -i images/mysql_8.0.30-8.6.tar
```

云南离线包和 Compose 固定使用以下隔离资源：

- 应用镜像标签：`yunnan-runtime:current`
- 推理镜像标签：`yunnan-inference-worker:current`
- 数据卷：`yunnan_backend_static`、`yunnan_mysql_data`、`yunnan_hf_cache`、`yunnan_miner_outputs`、`yunnan_miner_uploads`、`yunnan_inference_runtime`
- `yunnan_miner_tiles` 仅作为完整迁移归档恢复，不由当前 Compose 挂载。
- 项目空间数据目录：宿主机 `project_storage/`（按项目 ID 和资源 ID 隔离，不使用全局瓦片卷）

旧 `geoview_*` 数据卷，以及 `geoview-runtime:split-clean`、`geoview-inference-worker:current`、`geoview-jiangxi:gpu` 等旧镜像标签，均属于江西保留环境。云南部署不得挂载这些旧卷，也不得删除或覆盖这些旧资源。

### 3. 启动

```bash
export APP_IMAGE=yunnan-runtime:current
export INFERENCE_IMAGE=yunnan-inference-worker:current
export MYSQL_IMAGE=registry.openanolis.cn/openanolis/mysql:8.0.30-8.6
export OFFLINE_MAP_DIR="$(pwd)/maps/dali"
export ADMIN_PASSWORD='<管理员强密码>'
export SECRET_KEY='<随机会话密钥>'
export MYSQL_PASSWORD='<业务数据库强密码>'
export MYSQL_ROOT_PASSWORD='<MySQL root 强密码>'
```

启动前执行只读冲突检查；以下 7 个容器名和 7 个卷名全部不存在时，才允许继续启动：

```bash
for container in \
  yunnan-backend \
  yunnan-frontend \
  yunnan-miner-api \
  yunnan-miner-web \
  yunnan-inference-worker \
  yunnan-spatial-worker \
  yunnan-mysql; do
  if docker container inspect "$container" >/dev/null 2>&1; then
    echo "ERROR: container already exists: $container" >&2
    exit 1
  fi
done

for volume in \
  yunnan_backend_static \
  yunnan_mysql_data \
  yunnan_hf_cache \
  yunnan_miner_outputs \
  yunnan_miner_uploads \
  yunnan_inference_runtime \
  yunnan_miner_tiles; do
  if docker volume inspect "$volume" >/dev/null 2>&1; then
    echo "ERROR: volume already exists: $volume" >&2
    exit 1
  fi
done

docker compose --env-file .env -f docker-compose.prod.yml up -d
```

Compose 项目名固定为 `yunnan`；旧江西部署必须使用独立的 Compose project 名，不得与云南部署共用 project。

基础命令不申请 GPU，云南推理镜像会在 CPU 运行；它仍必须被加载，因为应用镜像中的 Torch 版本无法承载当前 DINOv3 模型。已安装 NVIDIA 宿主驱动和 NVIDIA Container Toolkit 时，使用：

```bash
export INFERENCE_IMAGE=yunnan-inference-worker:current
docker compose --env-file .env -f docker-compose.prod.yml -f docker-compose.gpu.yml up -d
docker exec yunnan-inference-worker python /app/docker/check-inference-runtime.py
```

随包归档镜像实测为 Python 3.10.12、Torch 2.7.0+cu128、MMCV 2.1.0、MMEngine 0.10.4、MMSeg 1.1.2；当前源码重建依赖已固定 MMSeg 1.2.2，重建环境与归档镜像不同。完整迁移应加载归档镜像，不在现场重建；离线部署机不现场下载 Python/CUDA 依赖。最终镜像不包含仅用于编译 MMCV 的 CUDA devel 工具链。

### 4. 验证

```bash
docker compose -f docker-compose.prod.yml ps
curl -I http://127.0.0.1:3000/
curl -I http://127.0.0.1:4000/
curl -I http://127.0.0.1:5008/api/auth/session
curl -I http://127.0.0.1:8000/api/auth/session
curl -I http://127.0.0.1:8000/tiles/projects/{projectId}/{resourceId}/{z}/{x}/{y}.png
docker exec yunnan-inference-worker python /app/docker/check-inference-runtime.py
```

先在 4000 登录，再使用已认证的浏览器网络面板（或携带会话 Cookie 的请求）访问完整 manifest 端点 `GET http://127.0.0.1:8000/api/projects/{projectId}/map/manifest`；读取真实的 `projectId`、`resourceId`、`z/x/y`，将所有占位符替换为实际值后执行项目级瓦片验证，返回 `200` 才表示该项目离线底图链路正常。旧全局统计和瓦片接口应返回 `410 Gone`。

### 5. 常见问题

检查云南容器名冲突：

```bash
docker container inspect yunnan-backend 2>/dev/null || true
docker container inspect yunnan-frontend 2>/dev/null || true
docker container inspect yunnan-miner-api 2>/dev/null || true
docker container inspect yunnan-miner-web 2>/dev/null || true
docker container inspect yunnan-inference-worker 2>/dev/null || true
docker container inspect yunnan-spatial-worker 2>/dev/null || true
docker container inspect yunnan-mysql 2>/dev/null || true
docker volume inspect yunnan_backend_static 2>/dev/null || true
docker volume inspect yunnan_mysql_data 2>/dev/null || true
docker volume inspect yunnan_hf_cache 2>/dev/null || true
docker volume inspect yunnan_miner_outputs 2>/dev/null || true
docker volume inspect yunnan_miner_uploads 2>/dev/null || true
docker volume inspect yunnan_inference_runtime 2>/dev/null || true
docker volume inspect yunnan_miner_tiles 2>/dev/null || true
```

以上查询任意一条有输出都表示资源冲突，不要继续执行 `docker compose ... up`；不要停止或删除已有资源。

底图灰底或瓦片 `404`：

```bash
docker compose -f docker-compose.prod.yml ps spatial-worker
docker compose -f docker-compose.prod.yml logs --tail=100 spatial-worker
find project_storage/projects/{projectId}/tiles/{resourceId} -name .active -print
```

重点检查：

- 项目摘要是否为 `map_ready=true`，底图任务是否为 `succeeded`
- `project_storage/projects/{projectId}/tiles/{resourceId}/.active` 是否存在
- 请求中的项目 ID 和资源 ID 是否与 manifest 一致
- `docker-compose.prod.yml` 是否使用当前镜像名 `yunnan-runtime:current` 和 `yunnan-inference-worker:current`

推理排查与 CPU 回滚见 `docs/inference_gpu_compatibility.md`。离线包交付前必须另存镜像 `docker image inspect` 结果和 SHA256，并记录 Torch/CUDA/MMCV/MMEngine/MMSeg 版本。

### 6. 项目级离线地图（当前版本）

旧全局 `/api/geojson`、`/api/stats` 和 `/tiles/{z}/{x}/{y}.png` 接口已停用并返回 `410 Gone`；所有地图请求都必须携带项目 ID。

宿主机运行数据目录为 `project_storage/`：

```text
project_storage/
├── incoming/
└── projects/{project_id}/
    ├── mines/{resource_id}/
    ├── basemaps/{resource_id}/
    ├── tiles/{resource_id}/{z}/{x}/{y}.png
    └── outputs/
```

新项目的矿山 KML/GeoJSON 直接在四步向导中上传。大型 TIF/TIFF 及同名 `.ovr`、`.tfw`、`.aux.xml`、`.prj`、`.enp` 文件必须先复制到 `project_storage/incoming/`，然后在第三步选择，不能在页面提交任意服务器路径。

`spatial-worker` 复用 `yunnan-runtime:current`，执行 GDAL 校验和 XYZ 离线切片。任务状态检查：

```bash
docker compose -f docker-compose.prod.yml ps spatial-worker
docker compose -f docker-compose.prod.yml logs --tail=100 spatial-worker
```

Compose 必须为后端和 Worker 保留镜像内的 `GDAL_DATA`、`PROJ_LIB`、`PROJ_DATA` 路径；否则网页导入时无法完成坐标转换和 GeoTIFF CRS 校验。

项目地图地址格式为 `#/map/{projectId}`。项目摘要只有在矿山资源和底图资源均为 `active` 时才返回 `map_ready=true`。瓦片验收地址格式为：

```text
http://127.0.0.1:8000/tiles/projects/{projectId}/{resourceId}/{z}/{x}/{y}.png
```

首次升级会自动把 `yunnan.kml`、现有指数表和大理 GeoTIFF 注册到云南种子项目，并在后台生成项目专属瓦片。10 GB 级 GeoTIFF 的复制和 8–15 级切片可能耗时较长；任务失败或取消不会替换已激活底图。每类资源仅保留当前版和上一版。

离线验收时断开外网，浏览器网络面板不得出现高德、天地图、OpenStreetMap、ArcGIS 或 OpenTopoMap 请求。大理与昆明应分别请求自己的 `/tiles/projects/...` 路径。
