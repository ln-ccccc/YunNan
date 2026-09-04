# GeoView 系统说明

本文档记录当前可运行版本的服务、目录、持久化和部署约束。

## 1. 服务与端口

| 服务 | 技术栈 | 宿主机端口 | 验证地址 |
| --- | --- | --- | --- |
| GeoView 前端 | Vue | 3000 | `http://localhost:3000/` |
| GeoView 后端 | Flask | 5008 | `http://localhost:5008/api/auth/session` |
| Miner 前端 | Vue/Vite | 4000 | `http://localhost:4000/` |
| Miner 后端 | Node/Express | 8000 | `http://localhost:8000/api/auth/session` |
| 推理 Worker | Python/MMSeg | 无 | 通过 `/api/inference/capabilities` 查询 |
| 空间 Worker | Python/GDAL | 无 | 通过项目空间任务接口查询 |
| MySQL | MySQL 8.0 | 默认不暴露 | 容器内 `3306` |

注意：Miner 后端根路径 `http://localhost:8000/` 返回 `404` 是正常现象。

## 2. 容器内关键目录

```text
/app/backend
/app/frontend
/app/miner
/app/backend/static
/app/miner/change_matrix_outputs
/app/miner/uploads
/app/backend/runtime/inference_jobs
/project_storage/incoming/dali
/project_storage/projects/{project_id}
```

## 3. Docker 编排

当前生产编排文件：

```text
docker-compose.prod.yml
docker-compose.gpu.yml
```

当前镜像：

```text
APP_IMAGE=yunnan-runtime:current
INFERENCE_IMAGE=yunnan-inference-worker:current
MYSQL_IMAGE=registry.openanolis.cn/openanolis/mysql:8.0.30-8.6
```

Web 与推理镜像分离；基础 Compose 让推理镜像在 CPU 运行，叠加 `docker-compose.gpu.yml` 后仅推理 Worker 获得 NVIDIA GPU。

命名卷：

```text
yunnan_backend_static
yunnan_mysql_data
yunnan_hf_cache
yunnan_miner_outputs
yunnan_miner_uploads
yunnan_inference_runtime
```

## 4. 异步推理

Flask 创建和查询 MySQL 任务，Miner Node 只代理请求，`inference-worker` 串行领取任务并复用已加载模型。新任务必须提交 `project_id`；矿山归属校验和输出目录均按项目隔离。

```text
POST /api/inference/jobs
GET  /api/inference/jobs/{job_id}
POST /api/inference/jobs/{job_id}/cancel
GET  /api/inference/capabilities
```

任务终态：`succeeded`、`succeeded_with_fallback`、`partial_failed`、`failed`、`cancelled`。

### 4.1 解译平台的项目路由

Miner 从当前项目进入地物分类或光谱指数页面时，会在 URL 和请求中携带 `project_id`。后端只读取该项目最新的活动矿山矢量资源，并只处理已绑定到该项目的矿山 FID，不会扫描或写入其他项目。

- TIFF 的有效像元与一个或多个矿山多边形相交：按命中的 FID 和年份发布到 `project_storage/projects/{project_id}/outputs`，矿山弹窗读取对应的地物分类、变化矩阵或光谱指数数据。
- TIFF 未命中当前项目、缺少项目上下文或缺少可用 CRS：结果只保留在 GeoView 解译平台，不写项目矿山数据。
- 地物分类的变化矩阵使用同一 FID 最近两个已有年份计算；只有一个年份时仍显示分类结果，但不生成虚假的变化矩阵。
- 光谱指数沿用同一项目匹配规则，并按 `FID + 指数类型 + 年份` 覆盖更新。

相关接口：

```text
POST /api/analysis/kml_roi_inference
POST /api/analysis/spectral_indices
GET  /api/projects/{project_id}/outputs/inference/{fid}/{filename}
```

浏览器的 CORS 预检 `OPTIONS` 请求不要求登录；实际业务请求仍经过会话鉴权。

NVIDIA 兼容、自检和回滚见 `docs/inference_gpu_compatibility.md`。

## 5. 离线底图

地图使用项目级资源，不再生成或挂载全局 `yunnan_miner_tiles`。大型底图先放入宿主机 `project_storage/incoming`，再由工作台登记；`spatial-worker` 将瓦片写入：

```text
project_storage/projects/{project_id}/tiles/{resource_id}/{z}/{x}/{y}.png
```

接口和路由必须携带项目 ID：

```text
#/map/{projectId}
GET /api/projects/{projectId}/map/manifest
GET /tiles/projects/{projectId}/{resourceId}/{z}/{x}/{y}.png
```

旧全局矿山、统计和瓦片接口返回 `410 Gone`。

## 6. 部署入口

- 离线部署：`offline_deployment_guide.md`
- Windows 完整离线迁移：根目录 `deploy_offline.ps1`
  - 仅校验：`powershell -ExecutionPolicy Bypass -File .\deploy_offline.ps1 -ValidateOnly`
  - 正式部署：`powershell -ExecutionPolicy Bypass -File .\deploy_offline.ps1`
  - 默认模式：脚本只使用 `docker-compose.prod.yml` 的 CPU 模式，不会自动叠加 `docker-compose.gpu.yml` 或启用 GPU
  - 宿主输入完整性：根目录 `HOST_SHA256SUMS` 固定校验源码、模型、`project_storage/` 和 `maps/dali/` 等 42,644 个文件（37.536GiB）；镜像和卷归档分别由 `images/SHA256SUMS`、`volumes/SHA256SUMS` 校验
  - 完整预检：三类清单合计读取 59,026,472,163 字节（54.973GiB，约 55.0GiB）的被校验数据，不代表整个目录大小；机械盘上可能耗时较长，运行期间不得修改迁移目录
  - 启动门禁：七个云南容器必须全部为 `running`；`yunnan-backend`、`yunnan-frontend`、`yunnan-miner-api`、`yunnan-miner-web`、`yunnan-mysql` 还必须为 `healthy`，两个 Worker 只要求 `running`
  - 安全：整个迁移目录包含 `.env`、`backend/.flaskenv`、数据库及业务文件，必须通过受控介质或可信链路传输
  - 失败处理：不得自行删除、重建或覆盖已创建的云南容器和卷；保留完整错误输出，由运维按 `yunnan.offline.restore=<runId>` 标签核对恢复归属后处理
- Docker 调试历史归档（禁止用于云南部署）：`docs/docker_hotfix_debug.md`
- 重启排障：`docs/docker_restart_guide.md`
